from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessCommand:
    executable: str
    arguments: list[str]
    cwd: Path
    environment: dict[str, str] | None = None
