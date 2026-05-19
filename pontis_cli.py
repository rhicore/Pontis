#!/usr/bin/env python3
"""
Pontis CLI.

Usage:
    pontis <project>                         # Open interactive agent chat
    pontis <project>:glob <ref>             # Execute one tool command directly
    pontis <project>:meta <ref> [options]
    pontis <project>:search <ref> <query>
    pontis <project>:cypher <query>
    pontis <project>:query <file> <sql>

`<project>` can be:
- a project name from `pontis.yml`
- a local directory path
"""
import argparse
import os
import shlex
import sys
from pathlib import Path


def _load_project_context(target: str) -> tuple[str, list[str], str]:
    """Resolve target to (project_path, active_projects, display_name)."""
    project_root = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, project_root)

    from storage.config import load_config

    cfg = load_config()

    if os.path.isdir(target):
        project_path = os.path.abspath(target)
        project_name = os.path.basename(project_path)
        return project_path, [project_name], project_name

    if target in cfg.projects:
        project_name = target
        src = cfg.resolve_source_path(project_name)
        graph = cfg.resolve_graph_path(project_name)
        if src:
            project_path = src
        elif graph:
            project_path = str(Path(graph).resolve().parent)
        else:
            project_path = os.getcwd()
        return os.path.abspath(project_path), [project_name], project_name

    raise SystemExit(f"Error: unknown project '{target}'. Use a project name from pontis.yml or a directory path.")


def _ensure_project_ready(project_path: str, active_projects: list[str]) -> None:
    project_name = active_projects[0]
    pontis_path = os.path.join(project_path, ".pontis")
    if os.path.exists(pontis_path):
        return

    project_root = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, project_root)
    from storage.config import load_config

    cfg = load_config()
    graph = cfg.resolve_graph_path(project_name)
    if graph and os.path.exists(graph):
        return

    print(f"No .pontis found in {project_path}")
    print("Run extractor first: python -m extractor.extract <folder>")
    raise SystemExit(1)


def _make_workspace(project_path: str, active_projects: list[str]):
    project_root = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, project_root)
    from storage.workspace import Workspace
    return Workspace(project_path=project_path, active_projects=active_projects)


def _parse_direct_args(command: str, argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=f"pontis :{command}", add_help=True)

    if command == "glob":
        parser.add_argument("ref")
        parser.add_argument("--offset", type=int, default=0)
        parser.add_argument("--limit", type=int, default=None)
    elif command == "meta":
        parser.add_argument("ref")
        parser.add_argument("--all", action="store_true")
        parser.add_argument("--property", nargs="+")
        parser.add_argument("--neighbor-label")
    elif command == "search":
        parser.add_argument("ref")
        parser.add_argument("query")
        parser.add_argument("--offset", type=int, default=0)
        parser.add_argument("--limit", type=int, default=None)
    elif command == "cypher":
        parser.add_argument("query")
        parser.add_argument("--offset", type=int, default=0)
        parser.add_argument("--limit", type=int, default=100)
    elif command == "query":
        parser.add_argument("file")
        parser.add_argument("sql")
        parser.add_argument("--limit", type=int, default=100)
    else:
        raise SystemExit(
            f"Error: unsupported direct command '{command}'. "
            f"Supported: glob, meta, search, cypher, query"
        )

    return parser.parse_args(argv)


def _run_direct_command(workspace, command: str, argv: list[str]) -> str:
    args = _parse_direct_args(command, argv)

    if command == "glob":
        from tool.glob.tool import glob_command
        return glob_command(workspace, ref=args.ref, offset=args.offset, limit=args.limit)
    if command == "meta":
        from tool.meta.tool import meta_command
        prop = None
        if args.property:
            prop = args.property if len(args.property) > 1 else args.property[0]
        return meta_command(
            workspace,
            ref=args.ref,
            all=args.all,
            property=prop,
            neighbor_label=args.neighbor_label,
        )
    if command == "search":
        from tool.search.tool import search_command
        return search_command(
            workspace,
            ref=args.ref,
            query=args.query,
            offset=args.offset,
            limit=args.limit,
        )
    if command == "cypher":
        from tool.cypher.tool import cypher_command
        return cypher_command(
            workspace,
            query=args.query,
            offset=args.offset,
            limit=args.limit,
        )
    if command == "query":
        from tool.query.tool import query_command
        return query_command(
            workspace,
            sql=args.sql,
            file=args.file,
            limit=args.limit,
        )
    raise SystemExit(f"Error: unsupported direct command '{command}'")


def _parse_repl_direct(line: str) -> tuple[str | None, list[str] | None]:
    text = line[1:].strip()
    if not text:
        return None, None
    parts = shlex.split(text)
    if not parts:
        return None, None
    return parts[0], parts[1:]


def _run_interactive(project_path: str, active_projects: list[str]) -> None:
    project_root = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, project_root)
    from agent.config import AgentSpec, create_agent

    workspace = _make_workspace(project_path, active_projects)
    spec = AgentSpec(projects=active_projects)
    agent = create_agent(project_path, spec=spec)

    print(f"\n\033[1mPontis Agent\033[0m — {project_path}")
    print(f"Model: {agent.config['model']}")
    print(f"Guardrails: {[g.__class__.__name__ for g in agent.guardrails]}")
    print("Type 'exit' or Ctrl+C to quit")
    print("Prefix input with ':' to run a direct workspace command locally, e.g. ':glob *'\n")

    while True:
        try:
            user_input = input("\033[36m你>\033[0m ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                print("Bye!")
                break
            if user_input.startswith(":"):
                try:
                    name, argv = _parse_repl_direct(user_input)
                    if not name:
                        result = "Error: empty direct command"
                    elif name in ("help", "?"):
                        result = (
                            "Direct commands: :glob, :meta, :search, :cypher, :query\n"
                            "Examples:\n"
                            ":glob '*:knowledge' --limit 5\n"
                            ":meta README --property detail\n"
                            ":search 'bird::*:knowledge' 'majority limit 1'\n"
                            ":cypher \"MATCH (n) RETURN n\"\n"
                            ":query restaurant.sqlite \"SELECT COUNT(*) FROM generalinfo\""
                        )
                    else:
                        result = _run_direct_command(workspace, name, argv or [])
                except SystemExit:
                    result = "Error: invalid direct command arguments"
                print(f"\n\033[33m命令>\033[0m {result}\n")
                continue

            response = agent.chat(user_input)
            print(f"\n\033[33m助手>\033[0m {response}\n")
        except KeyboardInterrupt:
            print("\nBye!")
            break
        except EOFError:
            print("\nBye!")
            break


def main():
    parser = argparse.ArgumentParser(
        description="Pontis - Interactive Data Analysis Agent",
    )
    parser.add_argument(
        "target",
        help=(
            "Project name / project folder for chat mode, or "
            "'<project>:<command>' for direct command mode"
        ),
    )
    parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments for direct command mode",
    )
    args = parser.parse_args()

    target = args.target
    if ":" in target:
        project_name, command = target.split(":", 1)
        if not project_name or not command:
            print("Error: direct mode must use '<project>:<command>'")
            raise SystemExit(1)
        project_path, active_projects, _ = _load_project_context(project_name)
        _ensure_project_ready(project_path, active_projects)
        workspace = _make_workspace(project_path, active_projects)
        print(_run_direct_command(workspace, command, args.args))
        return

    if args.args:
        print("Error: extra arguments are only allowed in direct mode '<project>:<command>'")
        raise SystemExit(1)

    project_path, active_projects, _ = _load_project_context(target)
    _ensure_project_ready(project_path, active_projects)

    project_root = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, project_root)
    _run_interactive(project_path, active_projects)


if __name__ == "__main__":
    main()
