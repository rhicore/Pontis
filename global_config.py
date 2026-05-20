"""Compatibility wrapper for the repository-root global_config.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_ROOT_CONFIG = Path(__file__).resolve().parent.parent / "global_config.py"
_SPEC = importlib.util.spec_from_file_location("_text2sql_global_config", _ROOT_CONFIG)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Could not load shared config from {_ROOT_CONFIG}")

_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

for _name in dir(_MODULE):
    if _name.isupper():
        globals()[_name] = getattr(_MODULE, _name)
