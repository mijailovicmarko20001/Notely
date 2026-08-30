"""Minimal, valid multi-page PDF builder for tests.

No PDF-authoring dependency exists in this project (pypdfium2/pypdf are
readers/mergers, not authors) -- this hand-rolls the smallest PDF structure
that both libraries parse correctly (verified against real page text,
rendering, and page-copying). Keep fixture text plain (letters/digits/
spaces/`\\n`) -- parens and backslashes aren't escaped by `make_pdf_bytes`
and would corrupt the content stream.
"""


def make_pdf_bytes(pages: list[str]) -> bytes:
    """Build a PDF with one page per string in `pages`, each page's text
    drawn via a single Tj text-show operator."""
    n_pages = len(pages)
    objs = []
    objs.append(b"<</Type/Catalog/Pages 2 0 R>>")  # 1: catalog
    kids = " ".join(f"{3 + i} 0 R" for i in range(n_pages))
    objs.append(f"<</Type/Pages/Kids[{kids}]/Count {n_pages}>>".encode())  # 2: pages
    content_obj_start = 3 + n_pages
    font_obj = content_obj_start + n_pages
    for i in range(n_pages):
        objs.append(
            (
                f"<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 {font_obj} 0 R>>>>"
                f"/MediaBox[0 0 200 100]/Contents {content_obj_start + i} 0 R>>"
            ).encode()
        )
    for text in pages:
        content = f"BT /F1 12 Tf 10 50 Td ({text}) Tj ET".encode()
        objs.append(b"<</Length " + str(len(content)).encode() + b">>\nstream\n" + content + b"\nendstream")
    objs.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")  # last: font

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj".encode() + body + b"endobj\n"
    xref_offset = len(out)
    n = len(objs) + 1
    out += f"xref\n0 {n}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += b"trailer<</Size " + str(n).encode() + b"/Root 1 0 R>>\n"
    out += b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF"
    return bytes(out)
