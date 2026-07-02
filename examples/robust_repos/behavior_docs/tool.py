#!/usr/bin/env python3
from __future__ import annotations

import os
import sys


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"--help", "-h"}:
        print("Usage: program COMMAND [ARGS]")
        print("Commands:")
        print("  echo TEXT       Print text.")
        print("  file FILE       Print a file path.")
        print("  env             Print APP_MODE.")
        print("Options:")
        print("  --config PATH   Load settings.toml.")
        return 0
    if argv[0] == "echo" and len(argv) == 2:
        print(argv[1])
        return 0
    if argv[0] == "file" and len(argv) == 2:
        print(argv[1])
        return 0
    if argv[0] == "env":
        print(os.environ.get("APP_MODE", "unset"))
        return 0
    if argv[0] == "--config" and len(argv) == 2:
        print(argv[1])
        return 0
    print("error: unknown command", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
