"""Shared process logging for the live end-to-end scripts."""

from __future__ import annotations

import sys
from pathlib import Path

from forgeflow.domain.process import ProcessCommand
from forgeflow.services.process_service import SyncProcessRunner


def run_command(command: ProcessCommand, log_path: Path, timeout: float = 1800) -> int:
    """Stream UTF-8 output to the console and save it after the process exits."""
    lines: list[str] = []

    def receive(channel: str, line: str) -> None:
        rendered = f"[{channel}] {line}"
        sys.stdout.buffer.write((rendered + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
        lines.append(rendered)

    code = SyncProcessRunner().run(command, receive, timeout=timeout)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return code
