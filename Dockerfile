# Two build targets:
#   base  — full image with all extras (channels/RAG/STT); used to serve/e2e.
#   test  — lean image with only what the hermetic pytest suite imports, so it
#           rebuilds fast (no torch/onnxruntime/fastembed/faster-whisper).
#
# Editable installs (`-e`) so a bind-mounted ./ shadows the baked-in copy and
# live edits run without a rebuild (see docker-compose `test`/`app` volumes).

FROM python:3.11-slim AS base
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -e ".[all]"
CMD ["onbo", "serve", "web"]

FROM python:3.11-slim AS test
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -e ".[test]"
CMD ["pytest", "-q"]
