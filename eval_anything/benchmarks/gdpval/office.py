"""Normalize the packaging defects in the pinned OpenAI Office files."""

import copy
import re
import tempfile
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

PROPERTIES = {
    "app": (
        "Properties",
        "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    ),
    "core": (
        "coreProperties",
        "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    ),
    "custom": (
        "Properties",
        "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties",
    ),
}


def office_copy(source, destination):
    if source.suffix.lower() not in {".docx", ".pptx"}:
        return source
    if destination.is_file():
        return destination
    with ZipFile(source) as archive:
        files = set(archive.namelist())
        changes = {}
        for name in files:
            if name != "[Content_Types].xml" and not name.endswith(".rels"):
                continue
            original = data = archive.read(name)
            if (
                name in ("[Content_Types].xml", "_rels/.rels")
                and b'xmlns:ns0="http://schemas.openxmlformats.org/package/2006/' in data
            ):
                data = (
                    data.replace(b"xmlns:ns0=", b"xmlns=")
                    .replace(b"<ns0:", b"<")
                    .replace(b"</ns0:", b"</")
                )
            if name.endswith(".rels"):
                data = re.sub(rb'(TargetMode="External")(?=</?Relationship)', rb"\1/>", data)
            if data != original:
                ElementTree.fromstring(data)
                changes[name] = data
        for rel in ElementTree.fromstring(archive.read("_rels/.rels")):
            name = rel.get("Target", "")
            if name in files:
                continue
            for key, (tag, ns) in PROPERTIES.items():
                if name == f"docProps/{key}.xml":
                    changes[name] = f'<{tag} xmlns="{ns}"/>'.encode()
        if not changes:
            return source
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as tmp:
            path = Path(tmp.name)
        try:
            with ZipFile(path, "w") as out:
                for info in archive.infolist():
                    name = info.filename
                    out.writestr(
                        copy.copy(info),
                        changes.pop(name) if name in changes else archive.read(info),
                    )
                for name, data in changes.items():
                    out.writestr(name, data)
            path.replace(destination)
        finally:
            path.unlink(missing_ok=True)
        return destination
