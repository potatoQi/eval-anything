import subprocess
import tempfile
from pathlib import Path


def docker(*args, **kwargs):
    return subprocess.check_output(["docker", *map(str, args)], text=True, **kwargs).strip()


def present(image):
    return bool(docker("image", "ls", "--quiet", "--no-trunc", image))


def build_args(proxy):
    return (
        ["--build-arg", f"HTTPS_PROXY={proxy}", "--build-arg", f"HTTP_PROXY={proxy}"]
        if proxy
        else []
    )


def build(image, recipe, proxy):
    with tempfile.TemporaryDirectory() as root:
        (Path(root) / "Dockerfile").write_text(recipe)
        docker("build", *build_args(proxy), "-t", image, root)
