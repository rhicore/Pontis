"""Compatibility wrapper for the relocated Neo4j instance CLI."""

from scripts.neo4j_instances import *  # noqa: F401,F403
from scripts.neo4j_instances import main


if __name__ == "__main__":
    raise SystemExit(main())
