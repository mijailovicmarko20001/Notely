"""DocConverter adapter backed by headless LibreOffice (soffice)."""

import shutil
import subprocess
from pathlib import Path

from ..ports import DocConverterError


class LibreOfficeDocConverter:
    """Real DocConverter, via `soffice --headless --convert-to pdf`."""

    def find_binary(self) -> str | None:
        """Locate the LibreOffice headless binary, including common macOS
        install paths that aren't necessarily on PATH. Exposed as its own
        method (not just an implementation detail of convert_to_pdf) so
        webui/preflight.py's environment check can use the exact same
        detection logic -- previously a second, slightly different
        (PATH-only, no macOS app-bundle fallback) copy of this lookup."""
        candidates = [
            shutil.which("soffice"),
            shutil.which("libreoffice"),
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        return None

    def convert_to_pdf(self, input_path: Path, out_dir: Path) -> Path:
        soffice = self.find_binary()
        if not soffice:
            raise DocConverterError(
                "soffice (LibreOffice) not found on PATH — required to render .pptx "
                "slides to images.\nInstall it with: brew install --cask libreoffice"
            )
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(input_path)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            raise DocConverterError(f"soffice conversion failed: {exc.stderr}") from exc
        pdf_path = out_dir / f"{Path(input_path).stem}.pdf"
        if not pdf_path.exists():
            raise DocConverterError(f"soffice conversion did not produce expected file: {pdf_path}")
        return pdf_path
