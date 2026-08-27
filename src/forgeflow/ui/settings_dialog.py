from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QVBoxLayout

from forgeflow.config import AppConfig


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ForgeFlow 설정")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        fields = {
            "modeling_root": ("modeling_local_mcp 경로", str(config.modeling_root)),
            "blender_agent_root": ("blender-prompt-agent 경로", str(config.blender_agent_root)),
            "blender_mcp_root": ("blender-control-mcp 경로", str(config.blender_mcp_root)),
            "blender_executable": ("Blender 실행 파일", str(config.blender_executable)),
            "jobs_root": ("작업 저장 루트", str(config.jobs_root)),
            "ollama_model": ("Ollama 모델", config.ollama_model),
            "ollama_base_url": ("Ollama 주소", config.ollama_base_url),
        }
        self.edits = {}
        for key, (label, value) in fields.items():
            edit = QLineEdit(value)
            self.edits[key] = edit
            form.addRow(label, edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self, original: AppConfig) -> AppConfig:
        values = {key: edit.text().strip() for key, edit in self.edits.items()}
        for key in ("modeling_root", "blender_agent_root", "blender_mcp_root", "blender_executable", "jobs_root"):
            values[key] = Path(values[key])
        return replace(original, **values)

