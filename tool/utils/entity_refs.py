"""Lightweight path/ref normalization helpers for tool layer."""


def path_ref_to_internal(ref: str) -> str:
    if "/" not in ref:
        return ref
    return ref.replace("/", "--")


def dotted_ref_to_path(ref: str) -> str:
    if "/" in ref or "." not in ref:
        return ref
    head, tail = ref.rsplit(".", 1)
    if not head or not tail:
        return ref
    return f"{head}/{tail}"
