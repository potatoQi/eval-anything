"""Serve pinned release artifacts to one sandbox for the duration of verification."""

import json
import re
import secrets
import shutil
import subprocess
import threading
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote, unquote

from envweaver import DownloadCache, Verifier

from ..docker import docker
from .pypi import simple_page

ASSETS = Path(__file__).parent / "assets"
DIGESTS = json.loads((ASSETS / "uv-0.9.5.json").read_text())
ORIGINS = {
    "uv": "https://github.com/astral-sh/uv/releases/download/0.9.5/",
    "python": "https://github.com/astral-sh/python-build-standalone/releases/download/",
}


@contextmanager
def release_mirror(gateway, client):
    prefix = "/" + secrets.token_urlsafe(24) + "/"
    cache = DownloadCache()
    wheels = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = unquote(self.path)
            if self.client_address[0] != client or not path.startswith(prefix):
                self.send_error(404)
                return
            key = path[len(prefix) :]
            kind, _, filename = key.partition("/")
            if kind == "simple" and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/", filename):
                project = filename[:-1]
                if self.command == "HEAD":
                    self.redirect("https://pypi.org/simple/" + filename)
                    return
                try:
                    page = simple_page(project, prefix, wheels)
                except (OSError, ValueError, KeyError):
                    self.redirect("https://pypi.org/simple/" + filename)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.pypi.simple.v1+json")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                return
            if kind == "wheels" and key in wheels:
                url, digest = wheels[key]
            elif (
                kind in ORIGINS
                and filename
                and all(x not in ("", ".", "..") for x in filename.split("/"))
            ):
                url, digest = ORIGINS[kind] + quote(filename, safe="/"), DIGESTS.get(key)
            else:
                self.send_error(404)
                return
            if not digest or self.command == "HEAD" or "Range" in self.headers:
                self.redirect(url)
                return
            with TemporaryDirectory() as folder:
                target = Path(folder) / "asset"
                try:
                    cache.download(url=url, checksum=digest, target=target)
                except OSError:
                    self.redirect(url)
                    return
                except ValueError:
                    self.send_error(502, "Artifact checksum mismatch")
                    return
                with target.open("rb") as stream:
                    self.send_response(200)
                    self.send_header("Content-Length", str(target.stat().st_size))
                    self.end_headers()
                    shutil.copyfileobj(stream, self.wfile, 1024 * 1024)

        do_HEAD = do_GET

        def redirect(self, url):
            self.send_response(307)
            self.send_header("Location", url)
            self.end_headers()

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer((gateway, 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://{gateway}:{server.server_port}{prefix}"
        finally:
            server.shutdown()
            thread.join()


class TerminalVerifier(Verifier):
    def run(self, verifier_instance, *, task_instance=None, files=None, env=None):
        materials = Path(self.entry).parent

        def original():
            return super(TerminalVerifier, self).run(
                verifier_instance, task_instance=task_instance, files=files, env=env
            )

        if (
            task_instance is None
            or task_instance.host is not None
            or task_instance.backend != "docker"
        ):
            return original()
        with ExitStack() as stack:
            try:
                networks = json.loads(
                    docker("inspect", "-f", "{{json .NetworkSettings.Networks}}", task_instance.id)
                )
                network = next(iter(networks.values())) if len(networks) == 1 else {}
                gateway, client = network.get("Gateway"), network.get("IPAddress")
                url = (
                    stack.enter_context(release_mirror(gateway, client))
                    if gateway and client
                    else None
                )
            except (OSError, subprocess.CalledProcessError):
                url = None
            if not url:
                return original()
            grader = Verifier(
                entry=ASSETS / "grader/run.py",
                runtime=self.runtime,
                timeout=self.timeout,
                required_env=self.required_env,
            )
            inputs = [
                {"source": materials / name, "target": name} for name in ("tests", "settings.json")
            ]
            return grader.run(
                verifier_instance,
                task_instance=task_instance,
                files=[*inputs, *(files or [])],
                env={**(env or {}), "TB_RELEASE_MIRROR": url},
            )


def with_download_cache(task):
    verifier = task.verifier
    materials = Path(verifier.entry).parent
    if json.loads((materials / "settings.json").read_text()).get("prepared_grader"):
        return task
    script = (materials / "tests/test.sh").read_text()
    if "https://astral.sh/uv/0.9.5/install.sh" not in script:
        return task
    return replace(
        task,
        verifier=TerminalVerifier(
            entry=verifier.entry,
            runtime=verifier.runtime,
            timeout=verifier.timeout,
            required_env=verifier.required_env,
        ),
    )
