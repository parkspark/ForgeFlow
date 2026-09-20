from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from forgeflow.config import AppConfig

PATH_FIELDS = {
    "modeling_root": "modeling_local_mcp 경로",
    "blender_agent_root": "blender-prompt-agent 경로",
    "blender_mcp_root": "blender-control-mcp 경로",
    "unity_agent_root": "unity_local_mcp 경로",
    "unity_mcp_root": "unity_mcp 경로",
    "blender_executable": "Blender 실행 파일",
    "jobs_root": "작업 저장 루트",
}


def validate_server_url(value: str) -> str:
    value = value.strip().rstrip("/")
    try:
        url = urlsplit(value)
        valid = (
            url.scheme in {"http", "https"}
            and bool(url.hostname)
            and url.username is None
            and url.password is None
            and not url.query
            and not url.fragment
            and not any(character.isspace() for character in value)
            and "\\" not in value
            and not url.netloc.endswith(":")
            and (url.port is None or 1 <= url.port <= 65535)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            "Ollama 주소는 http://127.0.0.1:11434처럼 입력하세요. 포트는 1~65535이며 로그인 정보·쿼리·공백은 사용할 수 없습니다."
        )
    return value


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent=None, *, load_models: bool = True):
        super().__init__(parent)
        self.setWindowTitle("ForgeFlow 설정")
        available = self.screen().availableGeometry()
        self.resize(min(760, available.width() - 40), min(620, available.height() - 80))
        self.original = config
        self.network = QNetworkAccessManager(self)
        self._generation = 0
        self._replies = set()
        layout = QVBoxLayout(self)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.edits = {}
        self.browse_buttons = {}
        for key, label in PATH_FIELDS.items():
            edit = QLineEdit(str(getattr(config, key)))
            self.edits[key] = edit
            edit.setAccessibleName(label)
            row = QHBoxLayout()
            row.addWidget(edit)
            button = QPushButton("찾아보기…")
            button.clicked.connect(lambda checked=False, field=key: self.browse_path(field))
            self.browse_buttons[key] = button
            row.addWidget(button)
            form.addRow(label, row)
        address = QLineEdit(config.ollama_base_url)
        self.edits["ollama_base_url"] = address
        address.editingFinished.connect(self.refresh_models)
        form.addRow("Blender Ollama 주소", address)
        self.model_combos = {}
        self.model_status = {}
        for key, title in (("ollama_model", "Blender 모델"), ("unity_agent_model", "Unity 모델")):
            combo = QComboBox()
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(12)
            combo.setAccessibleName(title)
            combo.setCurrentText(getattr(config, key))
            self.model_combos[key] = combo
            form.addRow(title, combo)
            status = QLabel("설치 모델 목록을 불러오거나 모델명을 직접 입력할 수 있습니다.")
            status.setWordWrap(True)
            self.model_status[key] = status
            form.addRow("", status)
        body_layout.addLayout(form)
        self.refresh_button = QPushButton("설치 모델 새로고침")
        self.refresh_button.clicked.connect(self.refresh_models)
        body_layout.addWidget(self.refresh_button)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        body_layout.addWidget(self.error_label)
        body_layout.addStretch()
        self.scroll_area.setWidget(body)
        body.setAutoFillBackground(False)
        self.scroll_area.viewport().setAutoFillBackground(False)
        layout.addWidget(self.scroll_area, 1)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("저장")
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setProperty(
            "actionRole", "primary"
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.finished.connect(self._cancel_requests)
        if load_models:
            QTimer.singleShot(0, self.refresh_models)

    def browse_path(self, key: str) -> None:
        current = self.edits[key].text().strip()
        if key == "blender_executable":
            selected, _ = QFileDialog.getOpenFileName(
                self, "Blender 실행 파일 선택", current, "실행 파일 (*.exe)"
            )
        else:
            selected = QFileDialog.getExistingDirectory(self, PATH_FIELDS[key], current)
        if selected:
            self.edits[key].setText(selected)

    def _cancel_requests(self, *_args) -> None:
        self._generation += 1
        for reply in list(self._replies):
            reply.abort()

    def refresh_models(self) -> None:
        self._cancel_requests()
        generation = self._generation
        unity_url = os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
        if "://" not in unity_url:
            unity_url = "http://" + unity_url
        endpoints = {}
        for key, address in (
            ("ollama_model", self.edits["ollama_base_url"].text()),
            ("unity_agent_model", unity_url),
        ):
            combo = self.model_combos[key]
            current = combo.currentText()
            combo.clear()
            combo.setCurrentText(current)
            try:
                address = validate_server_url(address)
            except ValueError as exc:
                self.model_status[key].setText(str(exc))
                continue
            endpoints.setdefault(address, []).append(key)
            self.model_status[key].setText(f"목록 확인 중 · {address}")
        for address, keys in endpoints.items():
            request = QNetworkRequest(QUrl(address + "/api/tags"))
            request.setTransferTimeout(5000)
            reply = self.network.get(request)
            self._replies.add(reply)
            reply.finished.connect(
                lambda response=reply, fields=keys, endpoint=address: self._models_ready(
                    response, fields, endpoint, generation
                )
            )

    def _models_ready(self, reply, keys, address, generation) -> None:
        self._replies.discard(reply)
        try:
            if generation != self._generation:
                return
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise ValueError(reply.errorString())
            payload = json.loads(bytes(reply.readAll()))
            models = payload["models"]
            if not isinstance(models, list):
                raise ValueError("모델 목록 형식 오류")
            names = sorted(
                {
                    item["name"]
                    for item in models
                    if isinstance(item, dict) and isinstance(item.get("name"), str)
                }
            )
            for key in keys:
                combo = self.model_combos[key]
                current = combo.currentText()
                combo.clear()
                combo.addItems(names)
                combo.setCurrentText(current)
                self.model_status[key].setText(f"설치 모델 {len(names)}개 · {address}")
        except (ValueError, KeyError, TypeError) as exc:
            for key in keys:
                self.model_status[key].setText(
                    f"목록 조회 실패 · {address}\n{exc}\n모델명을 직접 입력해 저장할 수 있습니다."
                )
        finally:
            reply.deleteLater()

    def value(self, original: AppConfig) -> AppConfig:
        values = {key: edit.text().strip() for key, edit in self.edits.items()}
        for key, label in PATH_FIELDS.items():
            if not values[key]:
                raise ValueError(f"{label}: 값을 입력하세요.")
            path = Path(values[key]).expanduser()
            if not path.is_absolute():
                raise ValueError(f"{label}: 절대 경로를 입력하세요.")
            if key == "blender_executable":
                if not path.is_file() or path.suffix.lower() != ".exe":
                    raise ValueError(f"{label}: 존재하는 .exe 파일을 선택하세요.")
            elif key == "jobs_root":
                ancestor = next((item for item in (path, *path.parents) if item.exists()), None)
                if ancestor is None or not ancestor.is_dir():
                    raise ValueError(f"{label}: 생성 가능한 폴더 경로를 입력하세요.")
            elif not path.is_dir():
                raise ValueError(f"{label}: 존재하는 폴더를 선택하세요.")
            values[key] = path
        values["ollama_base_url"] = validate_server_url(values["ollama_base_url"])
        for key, combo in self.model_combos.items():
            model = combo.currentText().strip()
            if not model or any(character.isspace() for character in model):
                raise ValueError("Blender·Unity 모델명을 선택하거나 공백 없이 입력하세요.")
            values[key] = model
        return replace(original, **values)

    def accept(self) -> None:
        try:
            self.value(self.original)
        except (ValueError, OSError) as exc:
            self.error_label.setText(f"저장할 수 없습니다: {exc}")
            QTimer.singleShot(0, lambda: self.scroll_area.ensureWidgetVisible(self.error_label))
            return
        self.error_label.clear()
        super().accept()
