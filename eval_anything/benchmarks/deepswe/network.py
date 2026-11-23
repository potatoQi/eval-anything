"""An internal Docker network with a gateway limited to one model endpoint."""

import json
import os
import secrets
import subprocess
import threading
import urllib.parse
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import urllib3

from ..docker import docker


def sandbox_session(network, address):
    # Docker can reassign an IP as soon as an attempt ends. The container ID stays unique.
    containers = json.loads(
        docker("network", "inspect", "--format", "{{json .Containers}}", network)
    )
    for container_id, endpoint in containers.items():
        if endpoint["IPv4Address"].partition("/")[0] == address:
            return uuid.uuid5(uuid.NAMESPACE_OID, container_id).hex
    raise ValueError("No sandbox at client address")


def open_model_response(pool, method, url, headers, body):
    origin = urllib.parse.urlsplit(url)
    for _ in range(11):
        response = pool.request(
            method,
            url,
            body=body,
            headers=headers,
            preload_content=False,
            decode_content=False,
            redirect=False,
        )
        location = response.get_redirect_location()
        if not location or (method == "POST" and response.status in (307, 308)):
            return response
        target = urllib.parse.urlsplit(urllib.parse.urljoin(url, location))
        response.close()
        response.release_conn()
        if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
            raise urllib3.exceptions.HTTPError("Model redirect leaves the configured origin")
        method, body, url = "GET", None, target.geturl()
    raise urllib3.exceptions.HTTPError("Too many model endpoint redirects")


@contextmanager
def model_network(base_url, api_key, state_path):
    parsed = urllib.parse.urlsplit(base_url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.query
    ):
        raise ValueError("base-url must be an http(s) API base URL without credentials/query")
    if "yibuapi" in parsed.hostname.lower():
        raise ValueError("Yibu requests must use the local Shadow service")
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    name = state["name"] if "name" in state else "deepswe-" + uuid.uuid4().hex[:12]
    token = state["token"] if "token" in state else secrets.token_urlsafe(32)
    pool = urllib3.PoolManager(maxsize=64, retries=False, timeout=1800)

    class Gateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def forward(self):
            if self.headers.get("Authorization") != "Bearer " + token:
                self.send_error(401)
                return
            allowed = {("GET", "/v1/models"), ("POST", "/v1/chat/completions")}
            if (self.command, self.path) not in allowed:
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400, "Invalid Content-Length")
                return
            if not 0 <= length <= 64 * 1024 * 1024:
                self.send_error(413)
                return
            body = self.rfile.read(length) if length else None
            headers = {"Content-Type": "application/json", "Authorization": "Bearer " + api_key}
            try:
                headers["X-Session-Id"] = self.headers.get("X-Session-Id") or sandbox_session(
                    name, self.client_address[0]
                )
            except (ValueError, subprocess.CalledProcessError):
                self.send_error(502, "Cannot identify model client on task network")
                return
            started = False
            response = None
            try:
                response = open_model_response(
                    pool,
                    self.command,
                    base_url.rstrip("/") + self.path.removeprefix("/v1"),
                    headers,
                    body,
                )
                self.send_response(response.status)
                for key in ("Content-Type", "Content-Encoding"):
                    if key in response.headers:
                        self.send_header(key, response.headers[key])
                sized = response.headers.get("Content-Length")
                chunked = sized is None and response.status not in (204, 304)
                if sized is not None:
                    self.send_header("Content-Length", sized)
                elif chunked:
                    self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                started = True
                while chunk := response.read1(65536):
                    if chunked:
                        self.wfile.write(f"{len(chunk):x}\r\n".encode())
                    self.wfile.write(chunk)
                    if chunked:
                        self.wfile.write(b"\r\n")
                    self.wfile.flush()
                if chunked:
                    self.wfile.write(b"0\r\n\r\n")
            except (OSError, urllib3.exceptions.HTTPError):
                self.close_connection = True
                if not started:
                    try:
                        self.send_error(502, "Model service unavailable")
                    except OSError:
                        pass
            finally:
                if response is not None:
                    response.close()
                    response.release_conn()

        do_GET = do_POST = forward

        def log_message(self, *args):
            pass

    network_args = ["--subnet", state["subnet"], "--gateway", state["gateway"]] if state else []
    docker("network", "create", "--internal", *network_args, name)
    server = thread = None
    try:
        spec = json.loads(docker("network", "inspect", name))[0]
        gateway = spec["IPAM"]["Config"][0]["Gateway"]
        server = ThreadingHTTPServer((gateway, state.get("port", 0)), Gateway)
        with open(os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as file:
            json.dump(
                {
                    "name": name,
                    "token": token,
                    "gateway": gateway,
                    "subnet": spec["IPAM"]["Config"][0]["Subnet"],
                    "port": server.server_port,
                },
                file,
            )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield name, f"http://{gateway}:{server.server_port}/v1", token
    finally:
        if thread and thread.is_alive():
            server.shutdown()
            thread.join()
        if server:
            server.server_close()
        pool.clear()
        docker("network", "rm", name)
