from __future__ import annotations

import os
from pathlib import Path


def same_path(left: str | Path, right: str | Path) -> bool:
    left_value = os.path.normcase(os.path.realpath(os.path.abspath(str(left))))
    right_value = os.path.normcase(os.path.realpath(os.path.abspath(str(right))))
    return left_value == right_value
