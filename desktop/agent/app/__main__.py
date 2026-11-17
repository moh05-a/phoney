"""Command line: `python -m app` runs the agent; `python -m app hooks ...` manages AI-tool hooks."""

from __future__ import annotations

import argparse
import sys

from app.core.config import default_data_dir, load_config


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app", description="Phoney desktop agent")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run", help="start the agent (default)")
    hooks = sub.add_parser("hooks", help="show, install or remove hooks for an AI coding tool")
    hooks.add_argument("action", choices=["show", "install", "uninstall"])
    hooks.add_argument("tool", choices=["claude-code", "codex", "claude-usage", "claude-permissions"])
    args = parser.parse_args()

    if args.cmd == "hooks":
        from app.integrations.hooks import command

        return command(args.action, args.tool, load_config(default_data_dir()).server.local_port)

    from app.main import run

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
