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
- Transcription is CPU-only in Docker by default — noticeably slower than
  running natively on a machine with a usable GPU. **If you're on Linux or
  Windows+WSL2 with an NVIDIA GPU**, opt into GPU passthrough instead:
  ```
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
  ```
  (needs the NVIDIA driver + [`nvidia-container-toolkit`](https://github.com/NVIDIA/nvidia-container-toolkit)
  installed on the host first — see `docker-compose.gpu.yml`'s comments).
  **On a Mac**, this isn't an option at all — Docker Desktop/colima can't
  pass any GPU (NVIDIA or Apple's own) through to a Linux container,
  regardless of chip. Use developer mode below with `WHISPER_BACKEND=mlx`
  for GPU acceleration on Apple Silicon instead.
- **No usable GPU at all** (older laptop, no NVIDIA/Apple Silicon)? Set
  `WHISPER_BACKEND` to `groq` or `openai` instead of a local backend, and
  transcription runs on that provider's hosted Whisper API rather than
  your CPU. Configure it either in the web UI's **Setup** tab (backend
  selector + the matching provider's API-key field + a "test key" button)
  or directly in `.env` (`WHISPER_BACKEND=groq` + `GROQ_API_KEY`, or
  `WHISPER_BACKEND=openai` + `OPENAI_API_KEY`). This is opt-in and never
  the default: unlike every local backend, it sends your lecture audio to
  a third party, so only turn it on if you're fine with that trade-off.
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

Run the test suite (scheduler + progress-parsing + stage-4 matcher logic;
`pytest` is a dev-only dependency, already in `requirements.txt`):

```
.venv/bin/pytest tests/ -q
```

Full technical documentation — architecture, engineering decisions, every
hardcoded value, and the improvement roadmap — lives in `DOCUMENTATION.md`
(`CLAUDE.md` holds the original design spec).

## License

MIT — see [`LICENSE`](LICENSE). This covers the pipeline/web-UI code only;
it does not grant any rights to the lecture videos, slide decks, or
generated notes you produce with it, which remain your course's own
copyrighted material (see the note above — personal study use only, no
redistribution).
