"""The ``fg-env`` command line: ``fg-env <command> [args...]`` (or ``python -m fg_env <command>``).

``fg-env --help`` lists the commands (check, run, preview, experiment, tournament, evaluate, trace, guide,
schema, ...).
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fg-env",
        description="Fareground Environment SDK — check, run, preview and experiment with environment contracts",
    )
    from . import __version__
    from .analysis.cli import add_analysis_commands
    from .cli import add_commands
    from .report.cli import add_report_command

    parser.add_argument("--version", action="version", version=f"fg-env {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<command>")
    add_commands(sub)
    add_analysis_commands(sub)
    add_report_command(sub)
    args = parser.parse_args(argv)
    code: int = args.func(args)
    return code


if __name__ == "__main__":
    sys.exit(main())
