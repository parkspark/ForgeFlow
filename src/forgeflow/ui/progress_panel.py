from time import monotonic

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit


PHASES = {
    'modeling_vram': '모델 생성 준비 · VRAM 해제',
    'modeling': '이미지에서 3D 모델 생성 중',
    'modeling_preview': '모델 미리보기 생성 중',
    'inspect': 'Blender 장면 검사 중',
    'blender_plan': 'Blender 실행 계획 작성 중',
    'blender': 'Blender 편집 적용 중',
    'rigging_vram': '리깅 준비 · VRAM 해제',
    'rigging': 'Humanoid 리깅 중',
    'rigging_preview_rest': '리깅 미리보기 생성 중 · 기본 자세',
    'rigging_preview_pose': '리깅 미리보기 생성 중 · 포즈',
}


class ProgressPanel(QWidget):
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.started_at = None
        self.active = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        row = QHBoxLayout()
        self.phase = QLabel('실행 대기')
        self.phase.setTextFormat(Qt.TextFormat.PlainText)
        self.phase.setWordWrap(True)
        self.elapsed = QLabel('경과 00:00')
        self.cancel = QPushButton('실행 취소')
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self._cancel)
        self.details = QPushButton('상세 로그 펼치기')
        self.details.setCheckable(True)
        self.details.toggled.connect(self._toggle)
        row.addWidget(self.phase, 1)
        row.addWidget(self.elapsed)
        row.addWidget(self.cancel)
        row.addWidget(self.details)
        layout.addLayout(row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setMaximumHeight(180)
        self.log.hide()
        layout.addWidget(self.log)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.hide()

    def _toggle(self, expanded):
        self.log.setVisible(expanded)
        self.details.setText('상세 로그 접기' if expanded else '상세 로그 펼치기')

    def _cancel(self):
        self.cancel.setEnabled(False)
        self.phase.setText('취소 요청 중 · 프로세스 종료를 기다리는 중')
        self.cancel_requested.emit()

    def on_event(self, event):
        if event.get('type') != 'stage_started':
            return
        if not self.active:
            self.started_at = monotonic()
            self.log.clear()
            self.active = True
            self.timer.start()
        self.show()
        self.cancel.setEnabled(True)
        self.phase.setText(PHASES.get(event.get('stage'), '작업 진행 중'))
        self.append(self.phase.text())
        self._tick()

    def _tick(self):
        seconds = int(monotonic() - self.started_at) if self.started_at is not None else 0
        self.elapsed.setText(f'경과 {seconds // 60:02d}:{seconds % 60:02d}')

    def append(self, line):
        if self.active:
            bar = self.log.verticalScrollBar()
            at_bottom = bar.value() >= bar.maximum()
            value = bar.value()
            self.log.appendPlainText(line)
            bar.setValue(bar.maximum() if at_bottom else value)

    def finish(self, operation, success, message):
        if not self.active:
            return
        self.append(message)
        self._tick()
        self.timer.stop()
        self.active = False
        self.cancel.setEnabled(False)
        self.phase.setText(('완료 · ' if success else '종료 · ') + message)
