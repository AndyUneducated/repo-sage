"""Driver that runs the Go bench binary across a parameter grid.

Phase 5 fills in result parsing + plotting; this stub fixes the CLI.
"""

from __future__ import annotations

import argparse
import itertools
import subprocess
from pathlib import Path

GO_BENCH = Path(__file__).resolve().parents[2] / "go-hnsw" / "bin" / "hnsw-bench"


def main() -> None:
    parser = argparse.ArgumentParser(description="SIFT-1M sweep driver")
    parser.add_argument("--M", nargs="+", type=int, default=[8, 16, 32])
    parser.add_argument("--efC", nargs="+", type=int, default=[100, 200, 400])
    parser.add_argument("--ef", nargs="+", type=int, default=[16, 32, 64, 128, 256])
    args = parser.parse_args()

    if not GO_BENCH.exists():
        raise SystemExit(f"go bench binary missing: {GO_BENCH} (run `make hnsw-build` first)")

    for m, efc, ef in itertools.product(args.M, args.efC, args.ef):
        cmd = [str(GO_BENCH), f"--M={m}", f"--efC={efc}", f"--ef={ef}"]
        print("$", " ".join(cmd))
        subprocess.run(cmd, check=False)


if __name__ == "__main__":
    main()
