"""
nakedcapitalism.com → Podcast Pipeline
Scrapes the daily Links post, converts each article to audio via edge-tts,
combines into a single MP3 episode, and updates the RSS feed for GitHub Pages.

Dependencies:
    pip install requests beautifulsoup4 trafilatura edge-tts pydub
    brew install ffmpeg   # (macOS) or: sudo apt install ffmpeg
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import textwrap
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

import cloudscraper
import edge_tts
import requests
import trafilatura
from bs4 import BeautifulSoup
from pydub import AudioSegment

# ──────────────────────────────────────────────
# CONFIG — edit these
# ──────────────────────────────────────────────
GITHUB_PAGES_URL = "https://connie817954.github.io/nakedcap-podcast"  # no trailing slash
PODCAST_TITLE = "Naked Capitalism Daily Links"
PODCAST_DESCRIPTION = "Daily Links from nakedcapitalism.com, converted to audio."
PODCAST_AUTHOR = "Naked Capitalism (TTS)"
PODCAST_EMAIL = "you@example.com"

TTS_VOICE = "en-US-AriaNeural"          # edge-tts voice
TTS_RATE = "+0%"                         # speed: "+10%" to speed up
MAX_ARTICLES = 20                        # cap articles per episode
MIN_TEXT_LENGTH = 200                    # skip articles shorter than this (chars)

# Paths (relative to this script)
BASE_DIR = Path(__file__).parent
AUDIO_DIR = BASE_DIR / "docs" / "audio"
RSS_FILE = BASE_DIR / "docs" / "feed.xml"
STATE_FILE = BASE_DIR / "state.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Shared session — cloudscraper handles Cloudflare challenges
SESSION = cloudscraper.create_scraper()


# ──────────────────────────────────────────────
# STEP 1: SCRAPE NAKED CAPITALISM LINKS POST
# ──────────────────────────────────────────────

def fetch_links_post() -> dict | None:
    """Find today's Links post and return {title, url, article_links}."""
    log.info("Fetching nakedcapitalism.com homepage…")
    resp = SESSION.get("https://www.nakedcapitalism.com/", timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Find the most recent post whose title contains "Links" (e.g. "Links 2/21/2025")
    for a in soup.select("h2.entry-title a, h1.entry-title a"):
        if re.search(r"\blinks\b", a.text, re.I):
            post_url = a["href"]
            post_title = a.text.strip()
            log.info(f"Found Links post: {post_title} → {post_url}")
            return _parse_links_post(post_url, post_title)

    log.warning("No Links post found on homepage.")
    return None


def _parse_links_post(url: str, title: str) -> dict:
    """Fetch the Links post and extract all outbound article links."""
    resp = SESSION.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    content_div = soup.select_one("div.entry-content")
    if not content_div:
        raise ValueError("Could not find entry-content div")

    articles = []
    seen = set()

    for a in content_div.find_all("a", href=True):
        href = a["href"].strip()
        # Skip internal NC links, anchors, and social media
        if any(x in href for x in ["nakedcapitalism.com", "#", "twitter.com",
                                    "facebook.com", "mailto:", "javascript:"]):
            continue
        if not href.startswith("http"):
            continue
        if href in seen:
            continue
        seen.add(href)

        link_text = a.get_text(strip=True)
        # Try to grab the surrounding sentence as a blurb (the NC commentary)
        blurb = ""
        parent = a.find_parent("p") or a.find_parent("li")
        if parent:
            blurb = parent.get_text(" ", strip=True)

        articles.append({"url": href, "title": link_text or href, "blurb": blurb})
        if len(articles) >= MAX_ARTICLES:
            break

    log.info(f"Found {len(articles)} article links in Links post.")
    return {"title": title, "url": url, "articles": articles}


# ──────────────────────────────────────────────
# STEP 2: EXTRACT ARTICLE TEXT
# ──────────────────────────────────────────────

def fetch_article_text(url: str) -> str | None:
    """Use trafilatura to extract clean article text."""
    try:
        resp = SESSION.get(url, timeout=10)
        text = trafilatura.extract(resp.text, include_comments=False,
                                   include_tables=False, no_fallback=False)
        if text and len(text) >= MIN_TEXT_LENGTH:
            return text
    except Exception as e:
        log.warning(f"Failed to fetch {url}: {e}")
    return None


# ──────────────────────────────────────────────
# STEP 3: TEXT → AUDIO (edge-tts)
# ──────────────────────────────────────────────

async def text_to_speech(text: str, output_path: Path) -> None:
    """Convert text to MP3 using edge-tts."""
    communicate = edge_tts.Communicate(text, TTS_VOICE, rate=TTS_RATE)
    await communicate.save(str(output_path))


def make_chapter_intro(index: int, title: str, blurb: str) -> str:
    """Create a spoken intro for each chapter."""
    intro = f"Chapter {index}. {title}."
    if blurb and blurb != title:
        # Trim blurb to ~300 chars to keep intro short
        short_blurb = textwrap.shorten(blurb, width=300, placeholder="…")
        intro += f" {short_blurb}"
    return intro


# ──────────────────────────────────────────────
# STEP 4: ASSEMBLE EPISODE
# ──────────────────────────────────────────────

SILENCE_BETWEEN_CHAPTERS_MS = 2000   # 2 second gap between chapters


def assemble_episode(chapter_paths: list[Path], output_path: Path) -> None:
    """Concatenate chapter MP3s into a single episode with silence between them."""
    log.info(f"Assembling {len(chapter_paths)} chapters into {output_path.name}…")
    combined = AudioSegment.empty()
    silence = AudioSegment.silent(duration=SILENCE_BETWEEN_CHAPTERS_MS)

    for i, path in enumerate(chapter_paths):
        seg = AudioSegment.from_mp3(str(path))
        combined += seg
        if i < len(chapter_paths) - 1:
            combined += silence

    combined.export(str(output_path), format="mp3", bitrate="64k",
                    tags={"title": output_path.stem})
    log.info(f"Episode saved: {output_path} ({len(combined)/1000:.0f}s)")


# ──────────────────────────────────────────────
# STEP 5: UPDATE RSS FEED
# ──────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"episodes": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def generate_rss(episodes: list[dict]) -> str:
    """Generate a podcast-compatible RSS 2.0 feed."""
    items = ""
    for ep in reversed(episodes):  # newest first
        pub_date = format_datetime(datetime.fromisoformat(ep["pub_date"]))
        items += f"""
    <item>
      <title>{_xml(ep["title"])}</title>
      <description>{_xml(ep["description"])}</description>
      <enclosure url="{ep["audio_url"]}" length="{ep["file_size"]}" type="audio/mpeg"/>
      <guid isPermaLink="false">{ep["guid"]}</guid>
      <pubDate>{pub_date}</pubDate>
      <itunes:duration>{ep["duration"]}</itunes:duration>
      <itunes:explicit>no</itunes:explicit>
    </item>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{_xml(PODCAST_TITLE)}</title>
    <link>https://www.nakedcapitalism.com/</link>
    <description>{_xml(PODCAST_DESCRIPTION)}</description>
    <language>en-us</language>
    <itunes:author>{_xml(PODCAST_AUTHOR)}</itunes:author>
    <itunes:owner>
      <itunes:name>{_xml(PODCAST_AUTHOR)}</itunes:name>
      <itunes:email>{_xml(PODCAST_EMAIL)}</itunes:email>
    </itunes:owner>
    <itunes:category text="News"/>
    <itunes:explicit>no</itunes:explicit>
    <image>
      <url>{GITHUB_PAGES_URL}/cover.jpg</url>
      <title>{_xml(PODCAST_TITLE)}</title>
      <link>https://www.nakedcapitalism.com/</link>
    </image>{items}
  </channel>
</rss>"""


def _xml(s: str) -> str:
    """Escape XML special characters."""
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


# ──────────────────────────────────────────────
# MAIN PIPELINE
# ──────────────────────────────────────────────

async def run_pipeline() -> None:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Scrape
    post = fetch_links_post()
    if not post:
        log.error("No Links post found. Exiting.")
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    episode_id = f"nc-links-{today}"
    state = load_state()

    # Skip if we already generated today's episode
    if any(ep["guid"] == episode_id for ep in state["episodes"]):
        log.info(f"Episode {episode_id} already exists. Nothing to do.")
        return

    # 2. Fetch article text + TTS for each article
    tmp_dir = BASE_DIR / "tmp_chapters"
    tmp_dir.mkdir(exist_ok=True)
    chapter_paths = []
    chapter_titles = []

    for i, article in enumerate(post["articles"], start=1):
        log.info(f"[{i}/{len(post['articles'])}] Processing: {article['title'][:60]}")

        # Fetch article text
        body_text = fetch_article_text(article["url"])
        if not body_text:
            log.warning(f"  Skipping (no usable text): {article['url']}")
            continue

        # Build full TTS text: intro + body
        intro = make_chapter_intro(i, article["title"], article["blurb"])
        full_text = f"{intro}\n\n{body_text}"

        # Truncate to ~8000 chars to keep TTS fast and episodes sane
        full_text = textwrap.shorten(full_text, width=8000, placeholder=" […end of article…]")

        chapter_path = tmp_dir / f"chapter_{i:02d}.mp3"
        try:
            await text_to_speech(full_text, chapter_path)
            chapter_paths.append(chapter_path)
            chapter_titles.append(article["title"])
            log.info(f"  ✓ TTS done → {chapter_path.name}")
        except Exception as e:
            log.warning(f"  TTS failed: {e}")

    if not chapter_paths:
        log.error("No chapters generated. Exiting.")
        shutil.rmtree(tmp_dir)
        return

    # 3. Assemble episode
    episode_filename = f"{episode_id}.mp3"
    episode_path = AUDIO_DIR / episode_filename
    assemble_episode(chapter_paths, episode_path)

    # Clean up tmp
    shutil.rmtree(tmp_dir)

    # 4. Compute metadata
    file_size = episode_path.stat().st_size
    audio = AudioSegment.from_mp3(str(episode_path))
    duration_secs = len(audio) // 1000
    duration_str = f"{duration_secs // 3600:02d}:{(duration_secs % 3600) // 60:02d}:{duration_secs % 60:02d}"

    episode_record = {
        "guid": episode_id,
        "title": post["title"],
        "description": f"Daily Links from Naked Capitalism. {len(chapter_paths)} articles: "
                       + "; ".join(chapter_titles[:5])
                       + ("…" if len(chapter_titles) > 5 else ""),
        "audio_url": f"{GITHUB_PAGES_URL}/audio/{episode_filename}",
        "file_size": file_size,
        "duration": duration_str,
        "pub_date": datetime.now(timezone.utc).isoformat(),
    }

    # 5. Update state + RSS
    state["episodes"].append(episode_record)
    state["episodes"] = state["episodes"][-30:]  # keep last 30 episodes
    save_state(state)

    RSS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RSS_FILE.write_text(generate_rss(state["episodes"]))
    log.info(f"RSS feed updated → {RSS_FILE}")
    log.info("✅ Pipeline complete!")


if __name__ == "__main__":
    asyncio.run(run_pipeline())
