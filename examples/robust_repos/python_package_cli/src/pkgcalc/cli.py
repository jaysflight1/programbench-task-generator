from __future__ import annotations

import sys


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in {"-h", "--help"}:
        print("Usage: pkgcalc COMMAND [ARGS]")
        print("Commands:")
        print("  echo TEXT")
        return 0
    if args[0] == "--version":
        print("pkgcalc 0.1")
        return 0
    if args[0] == "echo" and len(args) > 1:
        print(" ".join(args[1:]))
        return 0
    print(f"unknown command: {args[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
