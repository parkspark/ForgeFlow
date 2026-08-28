from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QPushButton, QSplitter, QVBoxLayout, QWidget
)


class ModelingPanel(QWidget):
    generate_requested = Signal()
    retry_requested = Signal()
    open_file_requested = Signal(str)
    open_folder_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.status = QLabel("작업을 선택하세요.")
        self.status.setObjectName("stageStatus")
        layout.addWidget(self.status)
        splitter = QSplitter()
        self.preview = QLabel("입력 이미지 미리보기")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(320, 280)
        self.preview.setObjectName("previewFrame")
        splitter.addWidget(self.preview)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("모델링 산출물"))
        self.artifacts = QListWidget()
        self.artifacts.itemDoubleClicked.connect(lambda item: self.open_file_requested.emit(item.data(256)))
        right_layout.addWidget(self.artifacts)
        row = QHBoxLayout()
        self.generate = QPushButton("모델 생성")
        self.generate.clicked.connect(self.generate_requested)
        self.retry = QPushButton("실패 재시도")
        self.retry.clicked.connect(self.retry_requested)
        self.folder = QPushButton("결과 폴더 열기")
        self.folder.clicked.connect(self.open_folder_requested)
        row.addWidget(self.generate)
        row.addWidget(self.retry)
        row.addWidget(self.folder)
        right_layout.addLayout(row)
        splitter.addWidget(right)
        layout.addWidget(splitter, 1)

    def set_job(self, job, busy: bool = False) -> None:
        state = job.stages["modeling"]
        self.status.setText(f"모델링: {state.status}" + (f" · {state.error}" if state.error else ""))
        pixmap = QPixmap(job.input_image_path)
        if not pixmap.isNull():
            self.preview.setPixmap(pixmap.scaled(420, 340, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.artifacts.clear()
        for artifact in job.artifacts:
            if artifact.stage == "modeling":
                path = Path(artifact.path)
                detail = f"{path.stat().st_size:,} bytes" if path.is_file() else "파일 없음"
                self.artifacts.addItem(f"{artifact.kind.upper()} · {path.name} · {detail}")
                self.artifacts.item(self.artifacts.count() - 1).setData(256, artifact.path)
        self.generate.setEnabled(not busy and state.status == "pending")
        self.retry.setEnabled(not busy and state.status in {"failed", "cancelled"})
        self.folder.setEnabled(True)
