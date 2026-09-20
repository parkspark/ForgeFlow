from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..status import set_status_badge


class ImagePreview(QLabel):
    def __init__(self, empty_text: str):
        super().__init__(empty_text)
        self._image = QPixmap()
        self._source = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setObjectName("previewFrame")

    def load(self, path: str | None, empty_text: str) -> None:
        try:
            source = Path(path) if path else None
            stat = source.stat() if source else None
            key = (path, stat.st_mtime_ns, stat.st_size) if stat else None
        except OSError:
            key = None
        if key is not None and key == self._source:
            return
        self._source = key
        self._image = QPixmap()
        if key is not None:
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid():
                reader.setScaledSize(
                    size.scaled(QSize(1600, 1600), Qt.AspectRatioMode.KeepAspectRatio)
                )
            self._image = QPixmap.fromImage(reader.read())
        self.clear()
        self.setToolTip(path or "")
        if self._image.isNull():
            self.setText(empty_text)
        else:
            self._scale()

    def _scale(self):
        if not self._image.isNull():
            self.setPixmap(
                self._image.scaled(
                    self.contentsRect().size() - QSize(20, 20),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._scale()


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
        layout.addWidget(self.status, 0, Qt.AlignmentFlag.AlignLeft)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.error)
        splitter = QSplitter()
        self.preview_tabs = QTabWidget()
        self.preview = ImagePreview("입력 이미지를 선택하면 여기에 표시됩니다.")
        self.result_preview = ImagePreview("모델 생성이 끝나면 결과 미리보기가 표시됩니다.")
        self.preview_tabs.addTab(self.preview, "입력 이미지")
        self.preview_tabs.addTab(self.result_preview, "생성 결과")
        splitter.addWidget(self.preview_tabs)
        self._job_id = None
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("모델링 산출물"))
        self.artifact_hint = QLabel("모델을 생성하면 GLB · BLEND · FBX 파일을 확인할 수 있습니다.")
        self.artifact_hint.setWordWrap(True)
        self.artifact_hint.setProperty("sectionCaption", "true")
        right_layout.addWidget(self.artifact_hint)
        self.artifacts = QListWidget()
        self.artifacts.itemDoubleClicked.connect(
            lambda item: self.open_file_requested.emit(item.data(256))
        )
        right_layout.addWidget(self.artifacts)
        row = QHBoxLayout()
        self.generate = QPushButton("모델 생성")
        self.generate.setProperty("actionRole", "primary")
        self.generate.clicked.connect(self.generate_requested)
        self.retry = QPushButton("실패 재시도")
        self.retry.clicked.connect(self.retry_requested)
        self.folder = QPushButton("결과 폴더 열기")
        self.folder.clicked.connect(self.open_folder_requested)
        for button in (self.generate, self.retry, self.folder):
            button.setEnabled(False)
        row.addWidget(self.generate)
        row.addWidget(self.retry)
        row.addWidget(self.folder)
        right_layout.addLayout(row)
        splitter.addWidget(right)
        layout.addWidget(splitter, 1)

    def set_job(self, job, busy: bool = False) -> None:
        state = job.stages["modeling"]
        set_status_badge(self.status, "모델링", state.status)
        self.error.setText(state.error or "")
        self.error.setVisible(bool(state.error))
        self.preview.load(
            job.input_image_path, "입력 이미지를 읽을 수 없습니다.\n작업의 원본 파일을 확인하세요."
        )
        generated = next(
            (
                item.path
                for item in reversed(job.artifacts)
                if item.stage == "modeling" and item.kind == "preview"
            ),
            None,
        )
        self.result_preview.load(
            generated,
            "생성 결과 미리보기가 아직 없습니다.\n완료 후에도 없다면 실행 로그를 확인하세요.",
        )
        if self._job_id != job.job_id:
            self.preview_tabs.setCurrentIndex(1 if generated else 0)
            self._job_id = job.job_id
        self.artifacts.clear()
        for artifact in job.artifacts:
            if artifact.stage == "modeling":
                path = Path(artifact.path)
                detail = f"{path.stat().st_size:,} bytes" if path.is_file() else "파일 없음"
                self.artifacts.addItem(f"{artifact.kind.upper()} · {path.name} · {detail}")
                self.artifacts.item(self.artifacts.count() - 1).setData(256, artifact.path)
                self.artifacts.item(self.artifacts.count() - 1).setToolTip(artifact.path)
        self.artifact_hint.setText(
            "파일을 두 번 클릭하면 연결된 앱으로 엽니다."
            if self.artifacts.count()
            else "아직 생성한 파일이 없습니다. ‘모델 생성’을 눌러 시작하세요."
        )
        self.generate.setEnabled(not busy and state.status == "pending")
        self.retry.setEnabled(not busy and state.status in {"failed", "cancelled"})
        self.retry.setVisible(state.status in {"failed", "cancelled"})
        self.folder.setEnabled(True)
