# Notely — lecture videos + slides → study notes

Turns recorded lectures (YouTube links) plus the professor's slide decks into
condensed per-slide study notes you can read in 20–30 minutes instead of
watching hours of video. Everything runs on your own computer.

## Quick start (students, no Python setup)

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Mac/Windows/Linux).
2. Download this project folder, open a terminal in it, and run:

   ```
   docker compose up
   ```

3. Open **http://localhost:8000** in your browser.
4. Follow the tabs left to right:
   - **Setup** — environment check; paste your Anthropic API key
     (get one at [console.anthropic.com](https://console.anthropic.com); generating
     notes for a full lecture costs a few cents). Everything except the final
     note-generation step works without a key.
   - **Lectures & Slides** — paste the YouTube playlist link, confirm the
     lecture order, upload the slide PDFs (one deck can cover several lectures).
   - **Run** — start the pipeline and watch progress. Transcription runs on
     your CPU: expect roughly the length of the lecture per lecture. You can
     close the tab and come back.
   - **Review** — the slide-matcher shows you the few matches it wasn't sure
     about; fix any that look wrong and notes rebuild automatically.
   - **Study Guide** — read or download the assembled markdown guide.

All your files (videos, notes, settings) live in the `data/` folder next to
`docker-compose.yml` — deleting the container never deletes your work.

### Notes & limitations

- First transcription downloads a speech-recognition model (~1.5 GB, one time).
- Unlisted YouTube videos work with just the link. *Private* videos (ones you
  must sign into YouTube to watch) are not supported in Docker mode.
- Transcription is CPU-only in Docker (no GPU passthrough) — noticeably
  slower than running natively on a machine with a usable GPU. If speed
  matters and you're comfortable with Python, prefer developer mode below.
- Downloading YouTube videos technically runs against YouTube's ToS; keep the
  downloads and generated notes for personal study only — don't redistribute.

## Developer mode (no Docker) — recommended if you have Python already

```
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
brew install ffmpeg tesseract tesseract-lang   # macOS
.venv/bin/uvicorn webui.main:app --port 8000
```

For an exact reproducible install (macOS arm64, matches the validated dev
environment) use `pip install -r requirements-lock.txt` instead; on other
platforms stick with `requirements.txt`.

The pipeline stages are also runnable individually — see `scripts/*.py --help`
for each stage's flags.

Full technical documentation — architecture, engineering decisions, every
hardcoded value, and the improvement roadmap — lives in `DOCUMENTATION.md`
(`CLAUDE.md` holds the original design spec).
