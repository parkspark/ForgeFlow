from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import __version__
from .ui.main_window import MainWindow


def _app_icon() -> Path:
    return Path(__file__).resolve().parent / "resources" / "forgeflow.ico"


def _configure_windows_identity() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ForgeFlow.Desktop.0.1")
    except (AttributeError, OSError):
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ForgeFlow Windows 데스크톱 앱")
    parser.add_argument("--smoke-test", action="store_true", help="창 생성 후 자동 종료")
    args = parser.parse_args(argv)
    if args.smoke_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _configure_windows_identity()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("ForgeFlow")
    # Windows appends the display name to explicit window titles. Keep it
    # empty so the main window title is shown exactly as configured.
    app.setApplicationDisplayName("")
    app.setApplicationVersion(__version__)
    icon = _app_icon()
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    window = MainWindow(run_environment_checks=not args.smoke_test)
    window.show()
    if args.smoke_test:
        QTimer.singleShot(2500, window.close)
    result = app.exec()
    if args.smoke_test:
        print("FORGEFLOW_UI_SMOKE_OK")
        return 0
    return result


if __name__ == "__main__":
    raise SystemExit(main())
