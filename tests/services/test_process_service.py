from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from forgeflow.domain.process import ProcessCommand
from forgeflow.services.job_service import JobService
from forgeflow.services.pipeline_service import PipelineService
from forgeflow.services.process_control import terminate_process_tree
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
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    child_pid_file = tmp_path / "child.pid"
    child_code = "import time; time.sleep(120)"
    parent_code = (
        "import subprocess,sys,time,pathlib; "
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(p.pid)); time.sleep(120)"
    )
    service = ProcessService()
    handles = []
    unrelated = subprocess.Popen(
        [sys.executable, "-c", child_code], creationflags=subprocess.CREATE_NO_WINDOW
    )
    try:
        service.start(ProcessCommand(sys.executable, ["-c", parent_code], tmp_path))
        assert service.process.waitForStarted(5000)
        deadline = time.monotonic() + 5
        while not child_pid_file.is_file() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.02)
        assert child_pid_file.is_file()
        for pid in (int(service.process.processId()), int(child_pid_file.read_text())):
            # Retain the original process object so PID reuse and lingering
            # handles cannot be mistaken for a surviving child process.
            handle = kernel32.OpenProcess(0x00100001, False, pid)
            assert handle, ctypes.get_last_error()
            handles.append(handle)
        service.cancel()
        qapp.processEvents()
        for handle in handles:
            assert kernel32.WaitForSingleObject(handle, 5000) == 0
        assert unrelated.poll() is None
    finally:
        if service.running:
            service.cancel()
        for handle in handles:
            if kernel32.WaitForSingleObject(handle, 0) == 258:
                kernel32.TerminateProcess(handle, 1)
                kernel32.WaitForSingleObject(handle, 5000)
            kernel32.CloseHandle(handle)
        terminate_process_tree(unrelated)
