from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class NextStepPanel(QGroupBox):
    def __init__(self, actions, parent=None):
        super().__init__("다음 단계", parent)
        layout = QVBoxLayout(self)
        self.guidance = QLabel("작업을 선택하면 다음 단계를 안내합니다.")
        self.guidance.setTextFormat(Qt.TextFormat.PlainText)
        self.guidance.setWordWrap(True)
        layout.addWidget(self.guidance)
        row = QHBoxLayout()
        self.buttons = {}
        for key, title in actions:
            button = QPushButton(title)
            button.setEnabled(False)
            self.buttons[key] = button
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.blocked_reasons = QLabel()
        self.blocked_reasons.setTextFormat(Qt.TextFormat.PlainText)
        self.blocked_reasons.setWordWrap(True)
        self.blocked_reasons.hide()
        layout.addWidget(self.blocked_reasons)
        self.navigation_hint = QLabel("버튼을 누르면 해당 단계의 설정 화면을 엽니다.")
        self.navigation_hint.setWordWrap(True)
        self.navigation_hint.hide()
        layout.addWidget(self.navigation_hint)

    def update_state(self, text, availability):
        self.guidance.setText(text)
        blocked = []
        any_ready = False
        for key, button in self.buttons.items():
            enabled, reason = availability.get(key, (False, "이 단계의 준비 상태를 확인하세요."))
            reason = reason or "이 단계의 준비 상태를 확인하세요."
            button.setEnabled(enabled)
            button.setToolTip(reason)
            button.setAccessibleDescription(reason)
            any_ready |= enabled
            if not enabled and reason.strip() != text.strip():
                blocked.append(f"{button.text()}: {reason}")
        self.blocked_reasons.setText("\n".join(blocked))
        self.blocked_reasons.setVisible(bool(blocked))
        self.navigation_hint.setVisible(any_ready)
