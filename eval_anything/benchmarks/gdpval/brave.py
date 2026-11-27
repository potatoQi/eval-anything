"""Forward GDPval search requests with one active Brave key per run."""

import secrets
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import urllib3

from ..docker import docker

SEARCH_PATH = "/res/v1/web/search"
SEARCH_URL = "https://api.search.brave.com" + SEARCH_PATH


@contextmanager
def brave_gateway(output, env, keys, network, proxy, image):
    label = docker(
        "image",
        "inspect",
        "--format",
        '{{index .Config.Labels "eval-anything.gdpval.brave-gateway"}}',
        image,
    )
    if label != "1":
        raise ValueError(f"Rebuild {image} with the GDPval Dockerfile to use multiple Brave keys")
    host = (
        "127.0.0.1"
        if network == "host"
        else docker("network", "inspect", "--format", "{{(index .IPAM.Config 0).Gateway}}", network)
    )
    if not host:
        raise ValueError("GDPval search requires a reachable Docker network gateway")

    state = output / ".brave-index"
    index = int(state.read_text()) if state.exists() else 0
    if not 0 <= index <= len(keys):
        raise ValueError("Invalid saved Brave key index")
    lock = threading.Lock()
    token = secrets.token_hex(32)
    http = urllib3.ProxyManager(proxy) if proxy else urllib3.PoolManager()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal index
            path, _, query = self.path.partition("?")
            if path != SEARCH_PATH:
                self.send_error(404)
                return
            if self.headers.get("X-Subscription-Token") != token:
                self.send_error(403)
                return
            with lock:
                used = index
            while used < len(keys):
                try:
                    reply = http.request(
                        "GET",
                        SEARCH_URL + "?" + query,
                        headers={"X-Subscription-Token": keys[used], "Accept": "application/json"},
                        redirect=False,
                        retries=False,
                        timeout=180,
                    )
                except urllib3.exceptions.HTTPError:
                    self.send_error(502, "Brave Search connection failed")
                    return
                if reply.status == 402:
                    with lock:
                        if index == used:
                            tmp = state.with_suffix(".tmp")
                            tmp.write_text(str(index + 1))
                            tmp.replace(state)
                            index += 1
                        used = index
                    if used < len(keys):
                        continue
                self.send_response(reply.status)
                for name in ("Content-Type", "Retry-After"):
                    if name in reply.headers:
                        self.send_header(name, reply.headers[name])
                self.send_header("Content-Length", str(len(reply.data)))
                self.end_headers()
                self.wfile.write(reply.data)
                return
            self.send_error(402, "All configured Brave Search keys returned HTTP 402")

        def log_message(self, *args):
            pass

    with http, ThreadingHTTPServer((host, 0), Handler) as server:
        bypass = ",".join(filter(None, [env.get("no_proxy"), env.get("NO_PROXY"), host]))
        env.update(
            BRAVE_API_KEY=token,
            GDPVAL_BRAVE_SEARCH_URL=f"http://{host}:{server.server_port}{SEARCH_PATH}",
            NO_PROXY=bypass,
            no_proxy=bypass,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield {}
        finally:
            server.shutdown()
            thread.join()
