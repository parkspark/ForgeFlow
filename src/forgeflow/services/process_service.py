from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from forgeflow.domain.process import ProcessCommand
from forgeflow.services.process_control import (
    terminate_process_tree,
    terminate_windows_process_tree,
)


class ProcessService(QObject):
    line_received = Signal(str, str)
    finished = Signal(int, object)
    started = Signal()
    failed_to_start = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(lambda: self._drain("OUT"))
        self.process.readyReadStandardError.connect(lambda: self._drain("ERR"))
        self.process.started.connect(self.started)
        self.process.finished.connect(lambda code, status: self.finished.emit(code, status))
        self.process.errorOccurred.connect(self._error)
        self._buffers = {"OUT": "", "ERR": ""}

    @property
    def running(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning

    def start(self, command: ProcessCommand) -> None:
        if self.running:
            raise RuntimeError("다른 외부 프로세스가 실행 중입니다.")
        self._buffers = {"OUT": "", "ERR": ""}
        self.process.setWorkingDirectory(str(command.cwd))
        if command.environment is not None:
            environment = QProcessEnvironment()
            for key, value in command.environment.items():
                environment.insert(key, value)
            self.process.setProcessEnvironment(environment)
        else:
            self.process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        self.process.setProgram(command.executable)
        self.process.setArguments(command.arguments)
        self.process.start()

    def cancel(self) -> None:
        if not self.running:
            return
        pid = int(self.process.processId())
        if os.name == "nt" and pid > 0:
            # Kill only the tree rooted at the QProcess we created.  In particular,
            # never use wsl --shutdown, which would affect unrelated WSL sessions.
            if terminate_windows_process_tree(pid):
                if self.process.waitForFinished(5000):
                    return
        self.process.terminate()
        if not self.process.waitForFinished(3000):
            self.process.kill()
            self.process.waitForFinished(3000)

    def _drain(self, channel: str) -> None:
        raw = (
            self.process.readAllStandardOutput()
            if channel == "OUT"
            else self.process.readAllStandardError()
        )
        text = bytes(raw).decode("utf-8", errors="replace")
        value = self._buffers[channel] + text
        lines = value.splitlines(keepends=True)
        self._buffers[channel] = ""
        for line in lines:
            if line.endswith(("\n", "\r")):
                self.line_received.emit(channel, line.rstrip("\r\n"))
            else:
                self._buffers[channel] = line

    def flush(self) -> None:
        for channel, value in self._buffers.items():
            if value:
                self.line_received.emit(channel, value)
        self._buffers = {"OUT": "", "ERR": ""}

    def _error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.failed_to_start.emit(self.process.errorString())


class SyncProcessRunner:
    def run(
        self,
        command: ProcessCommand,
        on_line: Callable[[str, str], None] | None = None,
        timeout: float | None = None,
    ) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        process = subprocess.Popen(
            [command.executable, *command.arguments],
            cwd=command.cwd,
            env=command.environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert process.stdout is not None
        output = process.stdout
        events: queue.Queue[str | BaseException | None] = queue.Queue(maxsize=1024)
        stopped = threading.Event()

        def enqueue(value: str | BaseException | None) -> None:
            while not stopped.is_set():
                try:
                    events.put(value, timeout=0.1)
                    return
                except queue.Full:
                    continue

        def read_output() -> None:
            try:
                for line in output:
                    if stopped.is_set():
                        break
                    enqueue(line)
            except BaseException as exc:
                enqueue(exc)
            finally:
                enqueue(None)

        def remaining() -> float | None:
            if deadline is None:
                return None
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout)
            return seconds

        reader = threading.Thread(target=read_output, name="forgeflow-process-output", daemon=True)
        reader.start()
        try:
            while True:
                try:
                    line = events.get(timeout=remaining())
                except queue.Empty:
                    raise subprocess.TimeoutExpired(process.args, timeout) from None
                if line is None:
                    break
                if isinstance(line, BaseException):
                    raise line
                if on_line:
                    on_line("OUT", line.rstrip("\r\n"))
            return process.wait(timeout=remaining())
        except BaseException:
            if process.poll() is None:
                terminate_process_tree(process)
            raise
        finally:
            stopped.set()
            reader.join(timeout=1)
            # A descendant can retain the pipe after its parent exits. Never
            # block cleanup acquiring a TextIOWrapper lock held by that reader.
            if not reader.is_alive():
                output.close()
