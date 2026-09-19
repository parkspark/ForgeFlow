from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..status import set_status_badge, status_text

STAGE_NAMES = {"modeling": "모델링", "blender": "Blender", "rigging": "리깅", "unity": "Unity"}


def modified_text(value):
    try:
        return (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            .astimezone()
            .strftime("%Y-%m-%d %H:%M")
        )
    except (ValueError, TypeError, AttributeError):
        return "시간 정보 없음"


@lru_cache(maxsize=128)
def thumbnail(path, modified_ns):
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid():
        reader.setScaledSize(size.scaled(QSize(56, 56), Qt.AspectRatioMode.KeepAspectRatio))
    return QPixmap.fromImage(reader.read())


class JobName(QLabel):
    def __init__(self, text):
        super().__init__()
        self.full_text = text
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMinimumWidth(0)
        self.setMinimumHeight(22)
        self.setToolTip(text)

    def resizeEvent(self, event):
        self.setText(
            self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight, self.width())
        )
        super().resizeEvent(event)


class JobDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.text = ""


class ProjectPanel(QWidget):
    new_requested = Signal()
    job_selected = Signal(str)
    settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        title = QLabel("ForgeFlow")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(QLabel("최근 작업"))
        self.jobs = QListWidget()
        self.jobs.setItemDelegate(JobDelegate(self.jobs))
        self.jobs.currentItemChanged.connect(self._selected)
        layout.addWidget(self.jobs, 1)
        self.details_button = QPushButton("작업 상세 보기")
        self.details_button.clicked.connect(self.show_details)
        self.details_button.setEnabled(False)
        layout.addWidget(self.details_button)
        row = QHBoxLayout()
        new_button = QPushButton("새 작업")
        new_button.clicked.connect(self.new_requested)
        settings = QPushButton("설정")
        settings.clicked.connect(self.settings_requested)
        row.addWidget(new_button)
        row.addWidget(settings)
        layout.addLayout(row)

    @staticmethod
    def _job_text(job) -> str:
        return (
            f"{job.name}\n{job.job_id}\n모델링 {status_text(job.stages['modeling'].status)} · "
            f"Blender {status_text(job.stages['blender'].status)} · 리깅 {status_text(job.stages['rigging'].status)} · "
            f"Unity {status_text(job.stages['unity'].status)}"
        )

    def _render_item(self, item, job):
        card = QWidget()
        card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(6, 7, 6, 7)
        preview = QLabel("이미지\n없음")
        preview.setObjectName("jobThumbnail")
        preview.setFixedSize(56, 56)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            path = Path(job.input_image_path)
            pixmap = thumbnail(str(path), path.stat().st_mtime_ns)
            if not pixmap.isNull():
                preview.setPixmap(pixmap)
        except OSError:
            pass
        layout.addWidget(preview)
        content = QVBoxLayout()
        content.setSpacing(3)
        content.addWidget(JobName(job.name))
        stage = job.current_stage if job.current_stage in STAGE_NAMES else "modeling"
        badge = QLabel()
        set_status_badge(badge, STAGE_NAMES[stage], job.stages[stage].status)
        content.addWidget(badge)
        updated = QLabel("수정 " + modified_text(job.updated_at))
        updated.setMinimumHeight(22)
        updated.setToolTip("최근 수정 시간 · PC 현지 시간")
        content.addWidget(updated)
        layout.addLayout(content, 1)
        item.setToolTip(self._job_text(job) + "\n최근 수정: " + modified_text(job.updated_at))
        self.jobs.setItemWidget(item, card)
        card.ensurePolished()
        item.setSizeHint(card.sizeHint())

    def set_jobs(self, jobs, selected_id: str | None = None) -> None:
        self.jobs.blockSignals(True)
        self.jobs.clear()
        selected_row = 0
        for index, job in enumerate(jobs):
            self.jobs.addItem(self._job_text(job))
            item = self.jobs.item(index)
            item.setData(256, job.job_id)
            self._render_item(item, job)
            if job.job_id == selected_id:
                selected_row = index
        if self.jobs.count():
            self.jobs.setCurrentRow(selected_row)
        self.jobs.blockSignals(False)
        self.details_button.setEnabled(self.jobs.currentItem() is not None)

    def upsert_job(self, job, index: int, selected_id: str | None = None) -> None:
        """Update one list item without rebuilding the complete widget."""
        self.jobs.blockSignals(True)
        item = None
        for row in range(self.jobs.count()):
            candidate = self.jobs.item(row)
            if candidate.data(256) == job.job_id:
                item = self.jobs.takeItem(row)
                break
        if item is None:
            item = QListWidgetItem()
        item.setText(self._job_text(job))
        item.setData(256, job.job_id)
        self.jobs.insertItem(max(0, min(index, self.jobs.count())), item)
        self._render_item(item, job)
        target_id = selected_id or job.job_id
        for row in range(self.jobs.count()):
            if self.jobs.item(row).data(256) == target_id:
                self.jobs.setCurrentRow(row)
                break
        self.jobs.blockSignals(False)
        self.details_button.setEnabled(self.jobs.currentItem() is not None)

    def show_details(self):
        item = self.jobs.currentItem()
        if item is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("작업 상세 정보")
        dialog.resize(560, 260)
        layout = QVBoxLayout(dialog)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setPlainText(item.toolTip())
        layout.addWidget(details)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _selected(self, current, _previous) -> None:
        self.details_button.setEnabled(current is not None)
        if current:
            self.job_selected.emit(current.data(256))
