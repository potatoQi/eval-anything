"""Runs without network or host mounts in a disposable rendering container."""

import base64
import io
import json
import mimetypes
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

OFFICE = {
    ".docx",
    ".doc",
    ".xlsx",
    ".xls",
    ".pptx",
    ".ppt",
    ".rtf",
    ".odt",
    ".ods",
    ".odp",
    ".fodp",
    ".html",
    ".svg",
}


def binary(mime, data):
    return {"mime": mime, "data": base64.b64encode(data).decode("ascii")}


def run(*args):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, timeout=180).stdout


def parts(name, data):
    suffix = Path(name).suffix.lower()
    yield {"text": "File: " + name}
    mime = mimetypes.guess_type(name)[0] or ""
    if (mime.startswith("image/") or suffix == ".psd") and suffix != ".svg":
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "PNG")
            yield binary("image/png", buf.getvalue())
        return
    if suffix not in OFFICE and suffix != ".pdf":
        yield {"text": data.decode("utf-8-sig")}
        return
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / ("source" + suffix)
        path.write_bytes(data)
        if suffix in OFFICE:
            if suffix == ".xlsx":
                from openpyxl import load_workbook

                workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=False)
                for sheet in workbook:
                    yield {
                        "text": sheet.title
                        + "\n"
                        + "\n".join(
                            "\t".join("" if c is None else str(c) for c in row)
                            for row in sheet.values
                        )
                    }
                workbook.close()
            run(
                "soffice",
                "-env:UserInstallation=" + (root / "profile").as_uri(),
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(root),
                str(path),
            )
            path = path.with_suffix(".pdf")
        yield {"text": run("pdftotext", "-layout", str(path), "-").decode()}
        run("pdftoppm", "-jpeg", "-scale-to", "1280", str(path), str(root / "page"))
        pages = sorted(root.glob("page-*.jpg"), key=lambda p: int(p.stem.split("-")[-1]))
        if not pages:
            raise ValueError("Document rendered no pages")
        for page in pages:
            yield binary("image/jpeg", page.read_bytes())


if __name__ == "__main__":
    os.environ["HOME"] = "/tmp"
    json.dump(list(parts(sys.argv[1], sys.stdin.buffer.read())), sys.stdout)
