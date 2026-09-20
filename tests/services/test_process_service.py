from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from forgeflow.domain.process import ProcessCommand
from forgeflow.services.job_service import JobService
from forgeflow.services.pipeline_service import PipelineService
from forgeflow.services.process_service import ProcessService, SyncProcessRunner


def test_external_process_failure_is_preserved(tmp_path: Path):
    lines = []
    code = SyncProcessRunner().run(
        ProcessCommand(
            sys.executable, ["-c", "import sys; print('engine failed'); sys.exit(7)"], tmp_path
        ),
        lambda channel, line: lines.append((channel, line)),
    )
    assert code == 7
    assert lines == [("OUT", "engine failed")]


@pytest.mark.parametrize(
    "script",
    [
        "import time; time.sleep(5)",
        "import sys,time; sys.stdout.write('partial'); sys.stdout.flush(); time.sleep(5)",
        "import time\nend=time.monotonic()+5\n"
        "while time.monotonic()<end:\n print('still running', flush=True)\n time.sleep(.01)",
    ],
    ids=["silent", "no-newline", "continuous-output"],
)
def test_sync_timeout_covers_output_reading(tmp_path, monkeypatch, script):
    started = []
    popen = subprocess.Popen

    def record_process(*args, **kwargs):
        process = popen(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", record_process)
    began = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as failure:
        SyncProcessRunner().run(
            ProcessCommand(sys.executable, ["-c", script], tmp_path), timeout=0.25
        )
    assert failure.value.timeout == 0.25
    assert time.monotonic() - began < 3.5
    assert started[0].poll() is not None


def test_sync_runner_delivers_utf8_and_trailing_partial_line(tmp_path):
    lines = []
    script = "import sys; sys.stdout.buffer.write('첫 줄\\n마지막 줄'.encode('utf-8'))"
    code = SyncProcessRunner().run(
        ProcessCommand(sys.executable, ["-c", script], tmp_path),
        lambda channel, line: lines.append((channel, line)),
        timeout=5,
    )
    assert code == 0
    assert lines == [("OUT", "첫 줄"), ("OUT", "마지막 줄")]


def test_pipeline_reuses_one_buffered_log_handle(tmp_path: Path):
    service = PipelineService(JobService(tmp_path / "jobs"), None, None, None)  # type: ignore[arg-type]
    log_path = tmp_path / "pipeline.log"
    service._prepare_log(log_path)
    handle = service._log_handle
    for index in range(130):
        service._on_line("OUT", f"line-{index}")
    service._prepare_log(log_path)
    assert service._log_handle is handle
    service._close_log()

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 130
    assert lines[0] == "[OUT] line-0"
    assert lines[-1] == "[OUT] line-129"


def test_cancel_terminates_only_started_windows_process_tree(tmp_path: Path, qapp):
    if os.name != "nt":
        return
    child_pid_file = tmp_path / "child.pid"
    child_code = "import time; time.sleep(120)"
    parent_code = (
        "import subprocess,sys,time,pathlib; "
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(p.pid)); time.sleep(120)"
    )
    service = ProcessService()
    service.start(ProcessCommand(sys.executable, ["-c", parent_code], tmp_path))
    assert service.process.waitForStarted(5000)
    deadline = time.monotonic() + 5
    while not child_pid_file.is_file() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert child_pid_file.is_file()
    parent_pid = int(service.process.processId())
    child_pid = int(child_pid_file.read_text())
    service.cancel()
    qapp.processEvents()

    def alive(pid: int) -> bool:
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False

    deadline = time.monotonic() + 5
    while (alive(parent_pid) or alive(child_pid)) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(parent_pid)
    assert not alive(child_pid)
