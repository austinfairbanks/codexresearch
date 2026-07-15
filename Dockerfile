ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}
USER root

ARG UV_VERSION=0.11.19
ARG USER_ID=1000
ARG GROUP_ID=1000

RUN python3 -m pip install --break-system-packages --no-cache-dir "uv==${UV_VERSION}" \
    && (getent group "${GROUP_ID}" >/dev/null || groupadd --gid "${GROUP_ID}" soleresearch) \
    && (getent passwd "${USER_ID}" >/dev/null || useradd --uid "${USER_ID}" --gid "${GROUP_ID}" --create-home soleresearch)
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev

USER ${USER_ID}:${GROUP_ID}
ENTRYPOINT ["/app/.venv/bin/sole-research"]
CMD ["--help"]
