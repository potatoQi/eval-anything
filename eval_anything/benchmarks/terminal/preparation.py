"""Prepare the pinned Terminal-Bench task environments before scheduling Eval."""

import hashlib
import json
import re
import shlex
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit

from envweaver import DownloadCache

from ..docker import build_args, docker, present
from .downloads import ASSETS, DIGESTS, ORIGINS

QEMU = {"qemu-alpine-ssh", "qemu-startup"}
TORCH = {"pytorch-model-recovery", "torch-pipeline-parallelism", "torch-tensor-parallelism"}


def identifier(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def network(apt_mirror, direct_hosts, retries, timeout):
    if not isinstance(direct_hosts, list) or any(
        not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host)
        for host in direct_hosts
    ):
        raise ValueError("direct_hosts must be a list of hostnames")
    if any(type(n) is not int or n < minimum for n, minimum in [(retries, 0), (timeout, 1)]):
        raise ValueError("retries must be a nonnegative integer and timeout a positive integer")
    if apt_mirror is not None and (
        not isinstance(apt_mirror, str)
        or not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._/-]*)?", apt_mirror)
    ):
        raise ValueError("apt_mirror must be an HTTP(S) mirror root without credentials")
    command = [
        "sh",
        "-c",
        (ASSETS / "network.sh").read_text(),
        "tb-network",
        (apt_mirror or "").rstrip("/"),
        str(retries),
        str(timeout),
        *direct_hosts,
    ]
    return command


def torch_grader(script):
    before, marker, after = script.partition("source $HOME/.local/bin/env\n")
    commands = [
        line.strip() for line in before.splitlines() if line.strip() and not line.startswith("#")
    ]
    if not marker or commands != [
        "apt-get update",
        "apt-get install -y curl",
        "curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh",
    ]:
        raise ValueError("Unexpected Terminal-Bench verifier bootstrap")
    return marker + after


def uvx_command(test_script):
    match = re.search(
        r"^uvx\s+(.+?)\s+pytest --ctrf ", test_script.replace("\\\n", " "), re.MULTILINE
    )
    args = shlex.split(match[1]) if match else []
    if (
        args[:2] != ["-p", "3.13"]
        or len(args) < 4
        or len(args) % 2
        or any(
            flag != "-w" or not re.fullmatch(r"[a-z0-9-]+==[0-9.]+", package)
            for flag, package in zip(args[2::2], args[3::2])
        )
    ):
        raise ValueError("Unrecognized official Torch verifier dependency command")
    return shlex.join(["/root/.local/bin/uvx", *args, "pytest", "--version"])


def prepared_image(task, proxy, index, direct_hosts, retries, timeout):
    name = task.folder.name
    arch = docker("image", "inspect", "-f", "{{.Architecture}}", task.image)
    machine = {"amd64": "x86_64", "arm64": "aarch64"}[arch]
    torch = name in TORCH
    version = "3.13.9" if torch else "3.12.12"
    py = f"python/20251014/cpython-{version}+20251014-{machine}-unknown-linux-gnu-install_only_stripped.tar.gz"
    files = {"python.tar.gz": py}
    destination = (
        f"/root/.local/share/uv/python/cpython-{version}-linux-{machine}-gnu"
        if torch
        else "/opt/tb21-python"
    )
    recipe = [
        f"FROM {task.image}",
        f"RUN --mount=type=bind,source=python.tar.gz,target=/tmp/python.tgz mkdir -p {destination} && tar xzf /tmp/python.tgz -C {destination} --strip-components=1"
        + (
            f" && ln -sf {destination}/bin/python3 /usr/local/bin/python3"
            if not torch
            else f" && (command -v python3 || ln -s {destination}/bin/python3 /usr/local/bin/python3)"
        ),
    ]
    if torch:
        files["uv.tar.gz"] = f"uv/uv-{machine}-unknown-linux-gnu.tar.gz"
        command = uvx_command((task.folder / "tests/test.sh").read_text())
        cache_id = "tb21-uv-" + identifier([machine, index, command])
        recipe += [
            f"RUN if [ ! -s /etc/ssl/certs/ca-certificates.crt ]; then mkdir -p /etc/ssl/certs && cp {destination}/lib/python3.13/site-packages/pip/_vendor/certifi/cacert.pem /etc/ssl/certs/ca-certificates.crt; fi",
            "RUN --mount=type=bind,source=uv.tar.gz,target=/tmp/uv.tgz mkdir -p /root/.local/bin && tar xzf /tmp/uv.tgz -C /root/.local/bin --strip-components=1 && echo 'export PATH=\"$HOME/.local/bin:$PATH\"' > /root/.local/bin/env",
            (
                f"RUN --mount=type=cache,id={cache_id},target=/root/.cache/uv,sharing=locked "
                f"export UV_DEFAULT_INDEX={shlex.quote(index)} UV_HTTP_RETRIES={retries} UV_HTTP_TIMEOUT={timeout} UV_CONCURRENT_DOWNLOADS=2; "
                f"(UV_OFFLINE=1 {command} || {command}) && cp -a /root/.cache/uv /opt/tb21-uv-cache"
            ),
            "RUN mkdir -p /root/.cache && if [ -d /root/.cache/uv ]; then rmdir /root/.cache/uv; fi && ln -s /opt/tb21-uv-cache /root/.cache/uv",
            f"RUN --network=none UV_OFFLINE=1 UV_DEFAULT_INDEX={shlex.quote(index)} {command}",
        ]
    recipe = "\n".join(recipe) + "\n"
    image = "eval-anything/tb21:" + identifier([task.image_id, files, recipe])
    if not present(image):
        print(f"Preparing {name} dependencies before Eval", flush=True)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for local, key in files.items():
                kind, filename = key.split("/", 1)
                url = ORIGINS[kind] + quote(filename, safe="/")
                proxies = {"http": proxy, "https": proxy} if proxy else None
                if urlsplit(url).hostname in direct_hosts:
                    proxies = {}
                DownloadCache().download(
                    url=url, checksum=DIGESTS[key], target=root / local, proxies=proxies
                )
            (root / "Dockerfile").write_text(recipe)
            args = build_args(proxy)
            if direct_hosts:
                args.extend(["--build-arg", "NO_PROXY=" + ",".join(direct_hosts)])
            docker("build", *args, "-t", image, root)
    return docker("image", "inspect", "-f", "{{.Id}}", image)
