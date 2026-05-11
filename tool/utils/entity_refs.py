"""Lightweight path/ref normalization helpers for tool layer."""


def path_ref_to_internal(ref: str) -> str:
    if "/" not in ref:
        return ref
    return ref.replace("/", "--")


def dotted_ref_to_path(ref: str) -> str:
    # Path refs are the only stable external syntax for structured entities.
    # Blindly rewriting dots breaks filenames like "books.sqlite" and relation
    # names like "orders.user_id->users.id".
    return ref
