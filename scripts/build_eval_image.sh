#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_TAG="${1:-pbgen-eval:py-c}"

docker build -t "${IMAGE_TAG}" "${ROOT}/docker/pbgen-eval"
