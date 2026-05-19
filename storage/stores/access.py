"""Runtime access objects returned from resolver pointer properties."""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class FileOpen:
    path: str

    def __call__(self, *args, **kwargs):
        return builtins.open(self.path, *args, **kwargs)


@dataclass(frozen=True)
class DbConnect:
    db_path: str
    connect: Callable
    table: str = ""
    view: str = ""
    column: str = ""
    fk: str = ""

    def __call__(self, *args, **kwargs):
        return self.connect(*args, **kwargs)
