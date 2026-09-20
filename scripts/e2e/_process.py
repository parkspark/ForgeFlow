"""Shared process logging for the live end-to-end scripts."""

from __future__ import annotations

import sys
from pathlib import Path

from forgeflow.domain.process import ProcessCommand
from forgeflow.services.process_service import SyncProcessRunner


def run_command(command: ProcessCommand, log_path: Path, timeout: float = 1800) -> int:
    """Persist each output line, including when execution fails or times out."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", newline="\n") as handle:

        def receive(channel: str, line: str) -> None:
            rendered = f"[{channel}] {line}\n"
            handle.write(rendered)
            handle.flush()
            if hasattr(sys.stdout, "buffer"):
                sys.stdout.buffer.write(rendered.encode("utf-8", errors="replace"))
                sys.stdout.buffer.flush()
            else:
                sys.stdout.write(rendered)
                sys.stdout.flush()

        try:
            return SyncProcessRunner().run(command, receive, timeout=timeout)
        except BaseException as exc:
            handle.write(f"[ERROR] {type(exc).__name__}: {exc}\n")
            handle.flush()
            raise
