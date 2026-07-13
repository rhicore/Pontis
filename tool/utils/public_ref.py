"""Select the public semantic tag used for each graph-ref path segment."""

from __future__ import annotations


_PUBLIC_LABEL_ORDER = (
    "db", "table", "view", "col", "fk", "rel", "overlap", "disambig",
    "knowledge", "pattern", "hint", "file", "dir", "schema", "topic",
    "table_group", "column_group", "source",
)


def public_label(labels: list[str]) -> str:
    label_set = set(labels or [])
    return next((label for label in _PUBLIC_LABEL_ORDER if label in label_set), "entity")


__all__ = ["public_label"]
