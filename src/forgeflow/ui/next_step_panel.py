from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout, QHBoxLayout, QPushButton


class NextStepPanel(QGroupBox):
    def __init__(self, actions, parent=None):
        super().__init__('다음 단계', parent)
        layout = QVBoxLayout(self)
        self.guidance = QLabel('작업을 선택하면 다음 단계를 안내합니다.')
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

    def update_state(self, text, availability):
        self.guidance.setText(text)
        for key, (enabled, reason) in availability.items():
            self.buttons[key].setEnabled(enabled)
            self.buttons[key].setToolTip(reason)
