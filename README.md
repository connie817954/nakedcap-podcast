# Naked Capitalism Daily Podcast

Scrapes the daily **Links** post from [nakedcapitalism.com](https://www.nakedcapitalism.com/),
converts each linked article to audio using Microsoft's free neural TTS (via `edge-tts`),
assembles them into a single MP3 episode with chapter breaks, and publishes a podcast
RSS feed via GitHub Pages.

---

## Setup (one-time, ~10 minutes)

### 1. Create the GitHub repo

```bash
gh repo create nakedcap-podcast --public
cd nakedcap-podcast
git init && git remote add origin git@github.com:YOUR_USERNAME/nakedcap-podcast.git
```

### 2. Copy these files into the repo

```
nakedcap-podcast/
├── scraper.py
├── requirements.txt
├── state.json            ← create this manually: {}
├── docs/                 ← GitHub Pages serves from here
│   └── audio/            ← MP3s go here (auto-created)
└── .github/
    └── workflows/
        └── daily_podcast.yml
```

Create the `state.json` file:
```bash
echo '{"episodes": []}' > state.json
mkdir -p docs/audio
```

Add a `cover.jpg` (3000×3000px recommended) to `docs/` — podcast apps display this as artwork.

### 3. Edit `scraper.py` — update these lines at the top

```python
GITHUB_PAGES_URL = "https://YOUR_USERNAME.github.io/nakedcap-podcast"
PODCAST_EMAIL    = "you@example.com"
```

### 4. Enable GitHub Pages

- Go to your repo → **Settings → Pages**
- Set Source to **Deploy from a branch**, branch = `main`, folder = `/docs`
- Save. GitHub will give you a URL like `https://YOUR_USERNAME.github.io/nakedcap-podcast`

### 5. Push everything

```bash
git add .
git commit -m "Initial setup"
git push -u origin main
```

### 6. Subscribe in your podcast app

Your RSS feed URL will be:
```
https://YOUR_USERNAME.github.io/nakedcap-podcast/feed.xml
```

Add this as a custom RSS feed in any podcast app:
- **Pocket Casts** → Add Podcast → search icon → paste URL
- **Overcast** → Add URL
- **Apple Podcasts** → Library → ... → Follow a Show → paste URL
- **Spotify** — does not support custom RSS; use the others

---

## Running manually (local)

```bash
pip install -r requirements.txt
brew install ffmpeg   # macOS; or: sudo apt install ffmpeg

python scraper.py
```

This generates audio in `docs/audio/` and updates `docs/feed.xml` and `state.json`.

---

## Configuration

All config is at the top of `scraper.py`:

| Variable | Default | Description |
|---|---|---|
| `TTS_VOICE` | `en-US-AriaNeural` | Edge TTS voice |
| `TTS_RATE` | `+0%` | Speed (+20% = faster) |
| `MAX_ARTICLES` | `20` | Articles per episode |
| `MIN_TEXT_LENGTH` | `200` | Skip very short articles |

### Choosing a voice

Run this to list all available voices:
```bash
python -c "import asyncio, edge_tts; asyncio.run(edge_tts.list_voices())" | grep Name
```

Good English options:
- `en-US-AriaNeural` — warm, conversational female (default)
- `en-US-GuyNeural` — clear male voice
- `en-GB-SoniaNeural` — British female
- `en-AU-NatashaNeural` — Australian female

---

## How it works

```
GitHub Actions (7am daily)
  ↓
scraper.py fetches nakedcapitalism.com
  ↓
Finds today's "Links" post
  ↓
For each outbound article link (up to 20):
    → Fetches article text via trafilatura
    → Synthesizes audio: [chapter intro] + [article body]
  ↓
Concatenates all chapters into one MP3 with 2s silence between
  ↓
Writes docs/audio/nc-links-YYYY-MM-DD.mp3
Updates docs/feed.xml (RSS)
Updates state.json (episode history)
  ↓
Git commits & pushes → GitHub Pages serves the feed
```

---

## Troubleshooting

**No Links post found** — NC sometimes posts Links later in the day.
The workflow can be re-run manually from the GitHub Actions tab.

**Article has no text** — Some articles are paywalled or JS-rendered.
`trafilatura` will return nothing and the article is skipped gracefully.

**Episode already exists** — The script checks `state.json` and won't re-generate
a day's episode if it already ran. Delete the entry from `state.json` to force regeneration.

**Audio dir getting large** — Each episode is ~30–60 MB. GitHub has a 1GB soft limit
for Pages. After ~6 months you may want to delete older MP3s from `docs/audio/`.
