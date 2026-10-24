"""Text out of a PDF, page by page, so every extracted figure can cite its page."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def pages(path: Path | str) -> list[dict]:
    from pypdf import PdfReader  # imported here so the rest of the package works without it

    reader = PdfReader(str(path))
    out = []
    for i, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception as e:  # a malformed page should not hide the others
            text = ""
            out.append({"page": i, "text": "", "error": str(e)})
            continue
        out.append({"page": i, "text": text})
    return out


def extract_to(path: Path, dest: Path) -> dict:
    """Write <dest> as JSON pages; report whether the PDF had a text layer at all."""
    ps = pages(path)
    dest.write_text(json.dumps(ps, indent=1, ensure_ascii=False), encoding="utf-8")
    chars = sum(len(p["text"]) for p in ps)
    return {"pages": len(ps), "chars": chars, "text_path": str(dest),
            "no_text_layer": chars < 20}  # likely a scan: the agent should read the image instead


if __name__ == "__main__":
    for p in pages(sys.argv[1]):
        print(f"--- page {p['page']} ---\n{p['text']}")
