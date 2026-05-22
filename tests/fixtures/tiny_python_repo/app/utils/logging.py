"""Tiny logging helper used everywhere."""

import sys


def log(message: str) -> None:
    sys.stderr.write(f"[demo] {message}\n")
