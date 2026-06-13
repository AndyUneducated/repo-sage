#!/usr/bin/env bash
# Fetch the SIFT-1M (TEXMEX) ANN benchmark dataset (~168 MB compressed, ~1 GB
# unpacked) into benchmarks/sift1m/data/. Idempotent: skips download if the
# base file is already present. The dataset is NOT committed (see .gitignore).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${HERE}/data"
TARBALL_URL="ftp://ftp.irisa.fr/local/texmex/corpus/sift.tar.gz"
MIRROR_URL="https://ann-benchmarks.com/sift.tar.gz" # fallback mirror
TARBALL="${DATA_DIR}/sift.tar.gz"

mkdir -p "${DATA_DIR}"

if [ -f "${DATA_DIR}/sift/sift_base.fvecs" ]; then
  echo "SIFT-1M already present at ${DATA_DIR}/sift/ — nothing to do."
  exit 0
fi

echo "Downloading SIFT-1M to ${TARBALL} ..."
if command -v curl >/dev/null 2>&1; then
  curl -fL --retry 3 -o "${TARBALL}" "${TARBALL_URL}" \
    || curl -fL --retry 3 -o "${TARBALL}" "${MIRROR_URL}"
elif command -v wget >/dev/null 2>&1; then
  wget -O "${TARBALL}" "${TARBALL_URL}" \
    || wget -O "${TARBALL}" "${MIRROR_URL}"
else
  echo "error: need curl or wget to download the dataset" >&2
  exit 1
fi

echo "Extracting ..."
tar -xzf "${TARBALL}" -C "${DATA_DIR}"
rm -f "${TARBALL}"

echo "Done. Files:"
ls -lh "${DATA_DIR}/sift/"
echo
echo "Run the sweep with:"
echo "  python benchmarks/sift1m/run_sweep.py --dataset-dir ${DATA_DIR}/sift --faiss --write-docs"
