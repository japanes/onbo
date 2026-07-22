#!/usr/bin/env bash
# Run onbo from the host venv with GPU support for faster-whisper.
#
# CTranslate2 (faster-whisper's backend) loads cuDNN/cuBLAS via the dynamic
# linker, which reads LD_LIBRARY_PATH at process start. The CUDA libs ship
# inside the venv (nvidia-cudnn-cu12 / nvidia-cublas-cu12), so we point the
# loader at them here before exec'ing onbo.
#
# Usage:
#   ./run.sh about                 # index self-docs into the `about` collection
#   ./run.sh kb seed               # load the starter FAQ
#   ./run.sh serve web             # run the web channel on the host (GPU + Ollama)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"

# Collect every nvidia/*/lib dir that ships in the venv.
NVLIBS="$("$VENV/bin/python" - <<'PY'
import os, nvidia
base = nvidia.__path__[0]
dirs = [os.path.join(base, d, "lib") for d in os.listdir(base)
        if os.path.isdir(os.path.join(base, d, "lib"))]
print(os.pathsep.join(dirs))
PY
)"

export LD_LIBRARY_PATH="${NVLIBS}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export ONBO_CONFIG_DIR="${ONBO_CONFIG_DIR:-$HERE/config}"

exec "$VENV/bin/onbo" "$@"
