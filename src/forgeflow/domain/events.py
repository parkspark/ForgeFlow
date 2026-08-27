from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PipelineEvent:
    type: str
    stage: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

