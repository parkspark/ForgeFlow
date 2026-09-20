from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..status import set_status_badge, status_text

STAGE_NAMES = {"modeling": "모델링", "blender": "Blender", "rigging": "리깅", "unity": "Unity"}
SEARCH_ROLE = int(Qt.ItemDataRole.UserRole) + 1


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
        self._selected_job_id = None
        layout = QVBoxLayout(self)
        title = QLabel("ForgeFlow")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(QLabel("최근 작업"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("작업 이름·ID·단계 검색")
        self.search.setAccessibleName("작업 검색")
        self.search.setToolTip("이름, 작업 ID, 현재 단계 또는 상태로 찾기 · Enter로 첫 결과 열기")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.search.returnPressed.connect(self._open_first_result)
        layout.addWidget(self.search)
        self.result_count = QLabel()
        layout.addWidget(self.result_count)
        self.selection_notice = QLabel("열려 있는 작업은 검색 결과에서 숨겨졌습니다.")
        self.selection_notice.setWordWrap(True)
        self.selection_notice.hide()
        layout.addWidget(self.selection_notice)
        self.jobs = QListWidget()
        self.jobs.setAccessibleName("최근 작업 목록")
        self.jobs.setItemDelegate(JobDelegate(self.jobs))
        self.jobs.currentItemChanged.connect(self._selected)
        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(self.jobs)
        self.empty_page = QWidget()
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.addStretch()
        self.empty_state = QLabel()
        self.empty_state.setWordWrap(True)
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_state)
        self.clear_search = QPushButton("검색 지우기")
        self.clear_search.clicked.connect(self.search.clear)
        empty_layout.addWidget(self.clear_search, alignment=Qt.AlignmentFlag.AlignCenter)
        empty_layout.addStretch()
        self.list_stack.addWidget(self.empty_page)
        layout.addWidget(self.list_stack, 1)
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
        self._apply_filter()

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
        item.setData(
            SEARCH_ROLE,
            f"{job.name} {job.job_id} {STAGE_NAMES[stage]} "
            f"{status_text(job.stages[stage].status)}".casefold(),
        )
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
        scroll = self.jobs.verticalScrollBar().value()
        previous_id = self._selected_job_id
        target_id = selected_id or previous_id
        with QSignalBlocker(self.jobs):
            self.jobs.clear()
            for index, job in enumerate(jobs):
                self.jobs.addItem(self._job_text(job))
                item = self.jobs.item(index)
                item.setData(Qt.ItemDataRole.UserRole, job.job_id)
                self._render_item(item, job)
            ids = [
                self.jobs.item(row).data(Qt.ItemDataRole.UserRole)
                for row in range(self.jobs.count())
            ]
            self._selected_job_id = target_id if target_id in ids else (ids[0] if ids else None)
            self._apply_filter()
        if previous_id == self._selected_job_id:
            self.jobs.verticalScrollBar().setValue(scroll)

    def upsert_job(self, job, index: int, selected_id: str | None = None) -> None:
        """Update one list item without rebuilding the complete widget."""
        scroll = self.jobs.verticalScrollBar().value()
        previous_id = self._selected_job_id
        with QSignalBlocker(self.jobs):
            item = None
            for row in range(self.jobs.count()):
                candidate = self.jobs.item(row)
                if candidate.data(Qt.ItemDataRole.UserRole) == job.job_id:
                    item = self.jobs.takeItem(row)
                    break
            if item is None:
                item = QListWidgetItem()
            item.setText(self._job_text(job))
            item.setData(Qt.ItemDataRole.UserRole, job.job_id)
            self.jobs.insertItem(max(0, min(index, self.jobs.count())), item)
            self._render_item(item, job)
            self._selected_job_id = selected_id or previous_id or job.job_id
            self._apply_filter()
        if previous_id == self._selected_job_id:
            self.jobs.verticalScrollBar().setValue(scroll)

    def _apply_filter(self, _text=None):
        terms = self.search.text().casefold().split()
        visible = 0
        selected = None
        with QSignalBlocker(self.jobs):
            for row in range(self.jobs.count()):
                item = self.jobs.item(row)
                matches = all(term in item.data(SEARCH_ROLE) for term in terms)
                item.setHidden(not matches)
                visible += int(matches)
                if matches and item.data(Qt.ItemDataRole.UserRole) == self._selected_job_id:
                    selected = item
            # Filtering is navigation, not a request to open a different job.
            self.jobs.setCurrentItem(selected)
        self.result_count.setText(
            f"검색 결과 {visible} / 전체 {self.jobs.count()}개" if terms else f"전체 {visible}개"
        )
        self.selection_notice.setVisible(bool(self._selected_job_id and selected is None and terms))
        self.details_button.setEnabled(selected is not None)
        self.list_stack.setCurrentWidget(self.jobs if visible else self.empty_page)
        self.empty_state.setText(
            "검색 결과가 없습니다.\n이름·ID·현재 단계나 상태를 바꿔 검색하세요."
            if terms and self.jobs.count()
            else "아직 작업이 없습니다.\n‘새 작업’에서 입력 이미지를 선택해 시작하세요."
        )
        self.clear_search.setVisible(bool(terms))

    def _open_first_result(self):
        for row in range(self.jobs.count()):
            item = self.jobs.item(row)
            if not item.isHidden():
                self.jobs.setCurrentItem(item)
                self.jobs.setFocus()
                return

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
            self._selected_job_id = current.data(Qt.ItemDataRole.UserRole)
            self.selection_notice.hide()
            self.job_selected.emit(self._selected_job_id)
