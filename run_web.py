#!/usr/bin/env python3
"""Pontis Web UI — Launch the web frontend."""
import argparse
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(description="Pontis Web UI")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    args = parser.parse_args()

    import uvicorn

    # Load the app directly from the hyphenated directory
    server_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "front-end", "server.py"
    )
    spec = importlib.util.spec_from_file_location("front_end.server", server_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = mod.app

    print(f"Pontis Web UI starting at http://{args.host}:{args.port}")
    print(f"Open http://localhost:{args.port}/?project=/path/to/your/project")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
