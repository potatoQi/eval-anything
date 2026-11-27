ARG BASE_IMAGE=python:3.13.12-slim-trixie@sha256:f1927c75e81efd1e091dbd64b6c0ecaa5630b38635a3d1c04034ac636e1f94c8
FROM ${BASE_IMAGE}
COPY system-packages.txt python-packages.txt /opt/aa/
ENV DEBIAN_FRONTEND=noninteractive
ARG SNAPSHOT_ROOT=https://snapshot.debian.org/archive
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources && \
    for suite in trixie trixie-updates trixie-security; do \
        archive=debian; if [ "$suite" = trixie-security ]; then archive=debian-security; fi; \
        echo "deb [check-valid-until=no signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] ${SNAPSHOT_ROOT}/${archive}/20260529T000000Z ${suite} main"; \
    done > /etc/apt/sources.list && \
    apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update && \
    xargs -a /opt/aa/system-packages.txt apt-get -o Acquire::Retries=3 install -y --no-install-recommends build-essential pkg-config libcairo2-dev && \
    dpkg-query -W -f='${db:Status-Status} ${Package}=${Version}\n' | sed -n 's/^installed //p' > /tmp/installed && \
    if grep -Fvx -f /tmp/installed /opt/aa/system-packages.txt; then exit 1; fi && \
    sed -i "s|${SNAPSHOT_ROOT}|https://snapshot.debian.org/archive|g" /etc/apt/sources.list && \
    rm -rf /tmp/installed /var/lib/apt/lists/*
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir -r /opt/aa/python-packages.txt
RUN python -m venv --system-site-packages /opt/harness && /opt/harness/bin/pip install --no-cache-dir 'stirrup[litellm]==0.2.2' litellm==1.111.0
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ARG PLAYWRIGHT_DOWNLOAD_HOST
RUN python -m playwright install chromium && useradd -m -u 1000 user
# Python source distributions need a compiler during installation.
RUN cut -d= -f1 /opt/aa/system-packages.txt > /tmp/keep && \
    dpkg-query -W -f='${Package}\n' | grep -Fvx -f /tmp/keep | xargs -r dpkg --purge && \
    rm /tmp/keep
ENV PATH=/opt/harness/bin:$PATH

# GDPval can route only Brave searches through its per-run gateway.
RUN python -c 'from importlib.util import find_spec; from pathlib import Path; \
    web = Path(find_spec("stirrup.tools.web").origin); source = web.read_text(); \
    url = "\"https://api.search.brave.com/res/v1/web/search\""; \
    assert source.count(url) == 1, "Unexpected Stirrup search implementation"; \
    web.write_text(source.replace(url, f"os.environ.get(\"GDPVAL_BRAVE_SEARCH_URL\", {url})"))'
LABEL eval-anything.gdpval.brave-gateway="1"
USER user
WORKDIR /home/user
