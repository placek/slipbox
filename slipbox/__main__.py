"""Standalone CLI entry point — `python -m slipbox …`.

The same command surface hermes exposes as `slipbox …` (see
`commands.setup_argparse`), usable without the host agent for scripting and the
scheduled jobs (`slipbox persist-accepted`, `slipbox digest`).
"""
from __future__ import annotations

import argparse

from . import commands


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="slipbox", description="Operate the slipbox knowledge base."
    )
    commands.setup_argparse(parser)
    args = parser.parse_args()
    handler = getattr(args, "func", None)
    if handler is None:
        print(commands.HELP)
        return
    handler(args)


if __name__ == "__main__":
    main()
