from __future__ import annotations

import sys
from pathlib import Path

from forgeflow.adapters.modeling_adapter import ProcessCommand
from forgeflow.services.process_service import SyncProcessRunner


def test_external_process_failure_is_preserved(tmp_path: Path):
    lines = []
    code = SyncProcessRunner().run(
        ProcessCommand(sys.executable, ["-c", "import sys; print('engine failed'); sys.exit(7)"], tmp_path),
        lambda channel, line: lines.append((channel, line)),
    )
    assert code == 7
    assert lines == [("OUT", "engine failed")]

