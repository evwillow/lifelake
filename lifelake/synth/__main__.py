"""python -m lifelake.synth --out demo_data --seed 7 --scale 1.0"""
from __future__ import annotations

import argparse
import json
import sys

from . import generate


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lifelake.synth", description="Write a synthetic data tree for the demo.")
    ap.add_argument("--out", default="demo_data", help="output folder (default: demo_data)")
    ap.add_argument("--seed", type=int, default=7, help="random seed; the same seed gives a byte-identical tree")
    ap.add_argument("--scale", type=float, default=1.0, help="multiplies every count (default: 1.0)")
    ap.add_argument("--overwrite", action="store_true", help="replace the source folders if they already exist")
    a = ap.parse_args(argv)
    try:
        truth = generate(a.out, seed=a.seed, scale=a.scale, overwrite=a.overwrite)
    except (FileExistsError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(truth, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
