FROM python:3.12-slim-bookworm
ENV UV_HTTP_TIMEOUT=180 UV_HTTP_RETRIES=4
RUN pip install --timeout 120 --no-cache-dir uv==0.10.9
WORKDIR /opt/ale
COPY env/host-deps.json env/install_host_deps.sh env/
RUN sed -i s,http://deb.debian.org,https://deb.debian.org,g /etc/apt/sources.list.d/debian.sources && \
    bash env/install_host_deps.sh && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock README.md ./
COPY ale_run ale_run
COPY tasks tasks
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-install-workspace && \
    uv sync --frozen --inexact --package ale-task-eval-deps --no-install-workspace
ENV PATH="/opt/ale/.venv/bin:${PATH}" PYTHONPATH=/opt/ale
CMD ["sleep", "infinity"]
