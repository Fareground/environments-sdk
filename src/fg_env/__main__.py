"""The ``fg-env`` command line: ``fg-env <command> [args...]`` (or ``python -m fg_env <command>``).

``fg-env --help`` lists the commands by workflow: start (write and check a contract), run, analyse.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import textwrap

#: The commands ``fg-env --help`` lists, grouped in the order a contract is built, run and analysed.
WORKFLOW = {
    "start": ("guide", "new", "engines", "author", "check", "preview", "playtest", "expand", "migrate", "schema"),
    "run": ("run", "experiment", "tournament", "evaluate", "trace", "conformance", "playthrough", "bench"),
    "analyse": ("sweep", "sensitivity", "calibrate", "optimise", "backtest", "highlights", "describe", "report"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fg-env", usage="fg-env [-h] [--version] <command> ...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Fareground Environment SDK — write, check, run and analyse environment contracts.\n"
                    "New here? Start with: fg-env guide authoring",
    )
    from . import __version__
    from .cli import add_commands
    from .cli.analysis import add_analysis_commands
    from .cli.report import add_report_command

    parser.add_argument("--version", action="version", version=f"fg-env {__version__}")
    # prog: a command's own usage line reads "fg-env <command> ...", not the whole top-level usage before it
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<command>", help=argparse.SUPPRESS,
                                prog="fg-env")
    add_commands(sub)
    add_analysis_commands(sub)
    add_report_command(sub)
    parser.epilog = _commands_by_workflow(sub)
    args = parser.parse_args(argv)
    code: int = args.func(args)
    return code


def _commands_by_workflow(sub: argparse._SubParsersAction) -> str:
    """The command list for ``--help``, by workflow. Each command's own ``--help`` gets the same line as its
    description."""
    helps = {action.dest: action.help or "" for action in sub._choices_actions}
    for name, text in helps.items():
        sub.choices[name].description = sub.choices[name].description or text
    width = max(shutil.get_terminal_size().columns - 2, 60)
    lines = []
    for group, names in WORKFLOW.items():
        lines.append(f"{group}:")
        for name in names:
            lines += textwrap.wrap(helps[name], width, initial_indent=f"  {name:<12} ", subsequent_indent=" " * 15)
        lines.append("")
    return "\n".join(lines).rstrip()


if __name__ == "__main__":
    sys.exit(main())
