from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from forgeflow.adapters.modeling_adapter import ProcessCommand


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
        if self.running:
            self.process.terminate()
            if not self.process.waitForFinished(3000):
                self.process.kill()

    def _drain(self, channel: str) -> None:
        raw = self.process.readAllStandardOutput() if channel == "OUT" else self.process.readAllStandardError()
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
    def run(self, command: ProcessCommand, on_line: Callable[[str, str], None] | None = None, timeout: float | None = None) -> int:
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
        for line in process.stdout:
            if on_line:
                on_line("OUT", line.rstrip("\r\n"))
        return process.wait(timeout=timeout)
