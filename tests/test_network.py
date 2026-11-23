import hashlib
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.client import HTTPConnection, IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from eval_anything.benchmarks.deepswe.network import model_network


@contextmanager
def serve(handler):
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as http:
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{http.server_port}"
        finally:
            http.shutdown()
            thread.join()


class QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass


class NetworkTests(unittest.TestCase):
    def test_gateway_reuses_connections_without_replaying_posts(self):
        calls = []
        first = threading.Event()
        finish = threading.Event()

        class Origin(QuietHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                data = self.rfile.read(int(self.headers["Content-Length"]))
                calls.append((self.client_address, self.headers["X-Session-Id"], data))
                if data == b"drop":
                    self.close_connection = True
                    return
                if data == b"sse":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.write(b"3\r\none\r\n")
                    self.wfile.flush()
                    first.set()
                    finish.wait(5)
                    self.wfile.write(b"3\r\ntwo\r\n0\r\n\r\n")
                    return
                self.send_response(429 if data == b"busy" else 200)
                self.send_header("Content-Length", "10" if data == b"cut" else str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                self.close_connection = data == b"cut"

        def docker(*args):
            if args[:2] == ("network", "inspect"):
                if "--format" in args:
                    return '{"sandbox":{"IPv4Address":"127.0.0.1/8"}}'
                return '[{"IPAM":{"Config":[{"Gateway":"127.0.0.1","Subnet":"127.0.0.0/8"}]}}]'
            return ""

        with (
            serve(Origin) as upstream,
            tempfile.TemporaryDirectory() as temp,
            patch("eval_anything.benchmarks.deepswe.network.docker", side_effect=docker),
            model_network(upstream + "/v1", "test", Path(temp) / "state.json") as (_, url, token),
        ):
            client = HTTPConnection(urlsplit(url).netloc, timeout=5)
            self.addCleanup(client.close)

            def post(body, **headers):
                client.request(
                    "POST",
                    "/v1/chat/completions",
                    body,
                    {"Authorization": "Bearer " + token, **headers},
                )
                return client.getresponse()

            for _ in range(8):
                self.assertEqual(post(b"ok").read(), b"ok")
            self.assertEqual(len({peer for peer, _, _ in calls}), 1)
            self.assertEqual(len({sid for _, sid, _ in calls}), 1)
            self.assertEqual(post(b"ok", **{"X-Session-Id": "second"}).read(), b"ok")
            self.assertEqual(calls[-1][1], "second")
            stream = post(b"sse")
            self.assertTrue(first.wait(5))
            try:
                self.assertEqual(stream.read(3), b"one")
            finally:
                finish.set()
            self.assertEqual(stream.read(), b"two")
            busy = post(b"busy")
            self.assertEqual((busy.status, busy.read()), (429, b"busy"))
            with self.assertRaises(IncompleteRead):
                post(b"cut").read()
            client.close()
            dropped = post(b"drop")
            self.assertEqual(dropped.status, 502)
            dropped.read()
            self.assertEqual([body for _, _, body in calls].count(b"drop"), 1)
            self.assertEqual(post(b"next").read(), b"next")
            client.close()

    def test_download_resumes_in_a_new_process_and_handles_ignored_range(self):
        stream = io.BytesIO()
        data = os.urandom(160000)
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            item = tarfile.TarInfo("source/item")
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
        blob = stream.getvalue()
        digest = hashlib.sha256(blob).hexdigest()
        for ranged in (True, False):
            seen = []

            class Origin(QuietHandler):
                def do_GET(self, seen=seen, ranged=ranged):
                    seen.append(self.headers.get("Range"))
                    partial = len(seen) == 1
                    offset = 65536 if ranged and seen[-1] == "bytes=65536-" else 0
                    self.send_response(206 if offset else 200)
                    if offset:
                        self.send_header(
                            "Content-Range", f"bytes {offset}-{len(blob) - 1}/{len(blob)}"
                        )
                    self.send_header("Content-Length", str(len(blob) - offset))
                    self.end_headers()
                    self.wfile.write(blob[:65536] if partial else blob[offset:])

            with (
                self.subTest(ranged=ranged),
                serve(Origin) as origin,
                tempfile.TemporaryDirectory() as temp,
            ):
                dest = Path(temp) / "out"
                cmd = [
                    sys.executable,
                    "-c",
                    "from eval_anything.sources import unpack;from pathlib import Path;import sys;unpack(sys.argv[1],sys.argv[2],Path(sys.argv[3]),None)",
                    origin + "/archive",
                    digest,
                    str(dest),
                ]
                env = dict(os.environ, no_proxy="127.0.0.1", NO_PROXY="127.0.0.1")
                first = subprocess.run(cmd, env=env, capture_output=True, check=False)
                self.assertNotEqual(first.returncode, 0)
                self.assertFalse(dest.exists())
                subprocess.run(cmd, env=env, capture_output=True, check=True)
                self.assertEqual(seen[:2], [None, "bytes=65536-"])
                self.assertEqual((dest / "item").read_bytes(), data)
                self.assertFalse(list(Path(temp).glob("*.part")))

    def test_pypi_keeps_resolution_metadata_and_refreshes_the_index(self):
        import json
        from copy import deepcopy
        from email.message import Message

        from eval_anything.benchmarks.terminal.pypi import simple_page

        url = "https://files.pythonhosted.org/packages/x/pkg-1-py3-none-any.whl"
        sha = "1" * 64
        page = {
            "meta": {"api-version": "1.4"},
            "name": "pkg",
            "files": [
                {
                    "filename": "pkg-1-py3-none-any.whl",
                    "hashes": {"sha256": sha},
                    "url": url,
                    "core-metadata": {"sha256": "2" * 64},
                    "requires-python": ">=3.11",
                    "yanked": "withdrawn",
                },
                {"filename": "pkg.tar.gz", "hashes": {}, "url": "https://example.org/pkg.tar.gz"},
            ],
        }
        newer = deepcopy(page)
        newer["files"][0]["yanked"] = False

        def response(body):
            stream = io.BytesIO(json.dumps(body).encode())
            stream.headers = Message()
            return stream

        wheels = {}
        with patch(
            "eval_anything.benchmarks.terminal.pypi.urlopen",
            side_effect=[response(page), response(newer)],
        ) as get:
            for expected in (page, newer):
                actual = json.loads(simple_page("pkg", "/test/", wheels))
                key = actual["files"][0]["url"].removeprefix("/test/")
                self.assertEqual(wheels[key], (url, "sha256:" + sha))
                self.assertEqual(
                    wheels[key + ".metadata"], (url + ".metadata", "sha256:" + "2" * 64)
                )
                actual["files"][0]["url"] = url
                self.assertEqual(actual, expected)
            self.assertEqual(get.call_count, 2)
