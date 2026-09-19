from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Artifact:
    kind: str
    path: str
    stage: str
    created_at: str
    version: int | None = None
    sha256: str | None = None
    parent_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "Artifact":
        return cls(**value)  # type: ignore[arg-type]
