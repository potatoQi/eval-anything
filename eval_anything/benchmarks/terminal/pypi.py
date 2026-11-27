"""Relay the public Simple API; only immutable wheel bytes are reusable."""

import gzip
import json
import re
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, urlopen


def simple_page(project, prefix, wheels):
    source = f"https://pypi.org/simple/{project}/"
    request = Request(
        source, headers={"Accept": "application/vnd.pypi.simple.v1+json", "Accept-Encoding": "gzip"}
    )
    with urlopen(request, timeout=15) as response:
        page = json.load(
            gzip.GzipFile(fileobj=response)
            if response.headers.get("Content-Encoding") == "gzip"
            else response
        )
    for file in page["files"]:
        filename, url = file["filename"], urljoin(source, file["url"])
        file["url"] = url
        sha = file["hashes"].get("sha256", "")
        if (
            not filename.endswith(".whl")
            or "/" in filename
            or not url.startswith("https://files.pythonhosted.org/packages/")
            or urlsplit(url).query
            or not re.fullmatch("[a-f0-9]{64}", sha)
        ):
            continue
        key = f"wheels/{sha}/{filename}"
        wheels[key] = (url, "sha256:" + sha)
        file["url"] = prefix + quote(key)
        metadata = file.get("core-metadata")
        metadata_sha = metadata.get("sha256", "") if isinstance(metadata, dict) else ""
        wheels[key + ".metadata"] = (
            url + ".metadata",
            "sha256:" + metadata_sha if re.fullmatch("[a-f0-9]{64}", metadata_sha) else None,
        )
    return json.dumps(page, separators=(",", ":")).encode()
