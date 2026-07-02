from __future__ import annotations

import sys


def usage() -> str:
    return """Usage: packcalc COMMAND [ARGS]

Commands:
  double NUM    Print NUM * 2.

Options:
  -h, --help    Show help.
  --version     Show version.
"""


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in {"-h", "--help"}:
        print(usage(), end="")
        return 0
    if args[0] == "--version":
        print("packcalc 1.0.0")
        return 0
    if args[0] == "double" and len(args) == 2:
        try:
            value = int(args[1])
        except ValueError:
            print(f"invalid integer: {args[1]}", file=sys.stderr)
            return 2
        print(value * 2)
        return 0
    print("unknown command", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

