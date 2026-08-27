from __future__ import annotations

import argparse
import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ForgeFlow Windows 데스크톱 앱")
    parser.add_argument("--smoke-test", action="store_true", help="창 생성 후 자동 종료")
    args = parser.parse_args(argv)
    if args.smoke_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("ForgeFlow")
    window = MainWindow()
    window.show()
    if args.smoke_test:
        QTimer.singleShot(2500, app.quit)
    result = app.exec()
    if args.smoke_test:
        print("FORGEFLOW_UI_SMOKE_OK")
    return result


if __name__ == "__main__":
    raise SystemExit(main())

