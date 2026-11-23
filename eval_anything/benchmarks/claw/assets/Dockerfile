ARG PYTHON_BASE_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_BASE_IMAGE}
WORKDIR /opt/claw
COPY pyproject.toml ./
COPY src ./src
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir ".[mock]" envweaver==0.0.2
COPY mock_services ./mock_services
COPY --link --from=adapter fixtures ./tasks
COPY --link tasks ./tasks
COPY --from=adapter services.py snapshot.py ./
ENV PYTHONPATH=/opt/claw/src
