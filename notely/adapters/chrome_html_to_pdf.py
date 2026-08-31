"""HtmlToPdf adapter backed by headless Chrome/Chromium's --print-to-pdf."""

import shutil
import subprocess
from pathlib import Path

from ..ports import HtmlToPdfError

CHROME_CANDIDATES = [
    "chromium",
    "chromium-browser",
    "google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


class ChromeHtmlToPdf:
    """Real HtmlToPdf, via `chrome --headless --print-to-pdf`."""

    def find_binary(self) -> str | None:
        for candidate in CHROME_CANDIDATES:
            path = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
            if path:
                return path
        return None

    def render(self, html_uri: str, pdf_path: Path) -> None:
        chrome = self.find_binary()
        if not chrome:
            raise HtmlToPdfError("no Chrome/Chromium found — install Google Chrome (mac) or chromium (linux)")

        pdf_path = Path(pdf_path)
        try:
            result = subprocess.run(
                [
                    chrome,
                    "--headless",
                    "--disable-gpu",
                    "--no-sandbox",
                    # lets MathJax finish typesetting before print
                    "--virtual-time-budget=30000",
                    f"--print-to-pdf={pdf_path}",
                    "--no-pdf-header-footer",
                    html_uri,
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise HtmlToPdfError(str(exc)) from exc

        if result.returncode != 0 or not pdf_path.exists():
            raise HtmlToPdfError(f"chrome print failed:\n{result.stderr[-1500:]}")
