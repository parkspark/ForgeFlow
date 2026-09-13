from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget, QStyledItemDelegate,
)
from .status import status_text, set_status_badge


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
        return (f"{job.name}\n{job.job_id}\n모델링 {status_text(job.stages['modeling'].status)} · "
                f"Blender {status_text(job.stages['blender'].status)} · 리깅 {status_text(job.stages['rigging'].status)} · "
                f"Unity {status_text(job.stages['unity'].status)}")

    def _render_item(self, item, job):
        card = QWidget()
        card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(6, 6, 6, 6)
        for text in (job.name, job.job_id):
            label = QLabel(text)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
        badges = QGridLayout()
        for index, (key, title) in enumerate((("modeling", "모델링"), ("blender", "Blender"), ("rigging", "리깅"), ("unity", "Unity"))):
            badge = QLabel()
            set_status_badge(badge, title, job.stages[key].status)
            badges.addWidget(badge, index // 2, index % 2)
        layout.addLayout(badges)
        item.setToolTip(self._job_text(job))
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

    def _selected(self, current, _previous) -> None:
        if current:
            self.job_selected.emit(current.data(256))
