from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)


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
        return (f"{job.name}\n{job.job_id}\n모델링 {job.stages['modeling'].status} · "
                f"Blender {job.stages['blender'].status} · 리깅 {job.stages['rigging'].status} · "
                f"Unity {job.stages['unity'].status}")

    def set_jobs(self, jobs, selected_id: str | None = None) -> None:
        self.jobs.blockSignals(True)
        self.jobs.clear()
        selected_row = 0
        for index, job in enumerate(jobs):
            self.jobs.addItem(self._job_text(job))
            item = self.jobs.item(index)
            item.setData(256, job.job_id)
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
        target_id = selected_id or job.job_id
        for row in range(self.jobs.count()):
            if self.jobs.item(row).data(256) == target_id:
                self.jobs.setCurrentRow(row)
                break
        self.jobs.blockSignals(False)

    def _selected(self, current, _previous) -> None:
        if current:
            self.job_selected.emit(current.data(256))
