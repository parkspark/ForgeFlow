from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
    QTextEdit, QVBoxLayout, QWidget,
)

from forgeflow.domain.job import Job, UnitySession, UnityTurn


class UnityPanel(QWidget):
    connect_requested = Signal(str)
    disconnect_requested = Signal()
    browse_project_requested = Signal()
    open_project_requested = Signal()
    import_fbx_requested = Signal()
    send_requested = Signal(str, bool, bool, bool)
    cancel_requested = Signal()
    accept_requested = Signal(str, str)
    reject_requested = Signal(str, str)
    repair_requested = Signal(str, str, bool, bool, bool)
    open_path_requested = Signal(str)
    focus_unity_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.job: Job | None = None
        self.session: UnitySession | None = None
        self._latest_turn: UnityTurn | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        connection = QGroupBox("Unity 연결")
        connection_layout = QVBoxLayout(connection)
        project_row = QHBoxLayout()
        self.project_path = QLineEdit()
        self.project_path.setPlaceholderText(r"C:\UnityProjects\MyGame")
        self.recent_projects = QComboBox()
        self.recent_projects.setMinimumWidth(220)
        self.recent_projects.setPlaceholderText("최근 프로젝트")
        self.recent_projects.currentTextChanged.connect(self._recent_selected)
        browse = QPushButton("선택")
        browse.clicked.connect(self.browse_project_requested)
        self.connect_button = QPushButton("연결")
        self.connect_button.clicked.connect(lambda: self.connect_requested.emit(self.project_path.text().strip()))
        self.disconnect_button = QPushButton("연결 해제")
        self.disconnect_button.clicked.connect(self.disconnect_requested)
        self.open_project_button = QPushButton("프로젝트 폴더 열기")
        self.open_project_button.clicked.connect(self.open_project_requested)
        for widget in (
            QLabel("프로젝트"), self.project_path, self.recent_projects, browse,
            self.connect_button, self.disconnect_button, self.open_project_button,
        ):
            project_row.addWidget(widget)
        connection_layout.addLayout(project_row)
        status_row = QHBoxLayout()
        self.agent_status = QLabel("Unity Agent: 연결 안 됨")
        self.mcp_status = QLabel("Unity MCP: 확인 전")
        self.bridge_status = QLabel("Editor Bridge: 확인 전")
        self.identity_status = QLabel("실제 프로젝트 identity: 확인 전")
        for widget in (self.agent_status, self.mcp_status, self.bridge_status, self.identity_status):
            status_row.addWidget(widget)
        status_row.addStretch()
        connection_layout.addLayout(status_row)
        self.restart_notice = QLabel(
            "새 로컬 모델 세션 — Unity 프로젝트 상태와 저장된 채팅 기록은 유지되지만 "
            "모델의 내부 대화 컨텍스트는 초기화되었습니다."
        )
        self.restart_notice.setWordWrap(True)
        connection_layout.addWidget(self.restart_notice)
        root.addWidget(connection)

        context = QGroupBox("작업 컨텍스트")
        context_layout = QFormLayout(context)
        self.job_label = QLabel("선택된 Job 없음")
        self.fbx_label = QLabel("없음 — FBX 없이도 Unity 채팅을 사용할 수 있습니다.")
        self.fbx_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        fbx_row = QHBoxLayout()
        fbx_row.addWidget(self.fbx_label, 1)
        self.import_button = QPushButton("Unity 프로젝트로 가져오기")
        self.import_button.clicked.connect(self.import_fbx_requested)
        fbx_row.addWidget(self.import_button)
        self.asset_label = QLabel("가져온 Asset 없음")
        self.asset_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.include_asset = QCheckBox("현재 FBX 컨텍스트 포함")
        context_layout.addRow("ForgeFlow Job", self.job_label)
        context_layout.addRow("Humanoid FBX", fbx_row)
        context_layout.addRow("Unity Asset", self.asset_label)
        context_layout.addRow("프롬프트", self.include_asset)
        root.addWidget(context)

        splitter = QSplitter(Qt.Orientation.Vertical)
        chat_group = QGroupBox("Unity 텍스트 채팅")
        chat_layout = QVBoxLayout(chat_group)
        self.chat = QPlainTextEdit()
        self.chat.setReadOnly(True)
        self.chat.setPlaceholderText("로컬 모델 응답, 도구 호출, 마일스톤, 오류, 스크린샷이 표시됩니다.")
        chat_layout.addWidget(self.chat, 1)
        self.screenshot_preview = QLabel("스크린샷 없음")
        self.screenshot_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.screenshot_preview.setMinimumHeight(80)
        self.screenshot_preview.setMaximumHeight(220)
        chat_layout.addWidget(self.screenshot_preview)
        self.input = QTextEdit()
        self.input.setPlaceholderText("예: 현재 활성 씬과 주요 GameObject를 분석해서 알려줘.")
        self.input.setMaximumHeight(105)
        chat_layout.addWidget(self.input)
        options = QHBoxLayout()
        self.analyze_screenshot = QCheckBox("정적 스크린샷 자동 분석")
        self.include_scene = QCheckBox("현재 씬 컨텍스트 포함")
        self.include_scene.setChecked(True)
        options.addWidget(self.analyze_screenshot)
        options.addWidget(self.include_scene)
        options.addStretch()
        self.send_button = QPushButton("전송")
        self.send_button.clicked.connect(self._send)
        self.cancel_button = QPushButton("실행 취소")
        self.cancel_button.clicked.connect(self.cancel_requested)
        options.addWidget(self.send_button)
        options.addWidget(self.cancel_button)
        chat_layout.addLayout(options)
        splitter.addWidget(chat_group)

        review_scroll = QScrollArea()
        review_scroll.setWidgetResizable(True)
        review_body = QWidget()
        review_layout = QVBoxLayout(review_body)
        review = QGroupBox("검토 — 세 상태를 별도로 판정")
        review_form = QFormLayout(review)
        self.execution_result = QLabel("Agent execution: 대기")
        self.automated_result = QLabel("Automated verification: unavailable")
        self.human_result = QLabel("Human review: pending")
        self.checks = QPlainTextEdit()
        self.checks.setReadOnly(True)
        self.checks.setMaximumHeight(150)
        self.review_note = QTextEdit()
        self.review_note.setPlaceholderText("검토 메모 또는 수정 요청을 입력하세요.")
        self.review_note.setMaximumHeight(90)
        review_form.addRow("에이전트", self.execution_result)
        review_form.addRow("자동 검증", self.automated_result)
        review_form.addRow("인간 검토", self.human_result)
        review_form.addRow("requested / measured / skipped / unmapped", self.checks)
        review_form.addRow("검토 메모", self.review_note)
        review_layout.addWidget(review)
        guidance = QLabel(
            "자동 실행은 완료됐습니다. Unity Play Mode에서 실제 동작을 확인한 뒤 "
            "승인하거나 수정 의견을 입력하세요. 생성된 씬을 열고 Play Mode에서 입력·애니메이션·"
            "카메라·물리·타이밍을 직접 확인해야 합니다."
        )
        guidance.setWordWrap(True)
        review_layout.addWidget(guidance)
        actions = QHBoxLayout()
        self.accept_button = QPushButton("결과 승인")
        self.accept_button.clicked.connect(self._accept)
        self.reject_button = QPushButton("수정 필요")
        self.reject_button.clicked.connect(self._reject)
        self.repair_button = QPushButton("실패/기존 결과 수정")
        self.repair_button.clicked.connect(self._repair)
        self.focus_button = QPushButton("Unity 창으로 이동")
        self.focus_button.clicked.connect(self.focus_unity_requested)
        self.screenshot_button = QPushButton("스크린샷 열기")
        self.screenshot_button.clicked.connect(lambda: self._open_latest("screenshot"))
        self.receipt_button = QPushButton("검증 영수증 열기")
        self.receipt_button.clicked.connect(lambda: self._open_latest("receipt"))
        self.log_button = QPushButton("실행 로그 열기")
        self.log_button.clicked.connect(lambda: self._open_latest("log"))
        self.scene_button = QPushButton("생성된 씬 열기 안내")
        self.scene_button.clicked.connect(lambda: self._open_latest("scene"))
        for button in (
            self.accept_button, self.reject_button, self.repair_button, self.focus_button,
            self.screenshot_button, self.receipt_button, self.log_button,
            self.scene_button,
        ):
            actions.addWidget(button)
        review_layout.addLayout(actions)
        review_scroll.setWidget(review_body)
        splitter.addWidget(review_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)
        self.set_session(None)

    def _recent_selected(self, text: str) -> None:
        if text and text != self.project_path.text():
            self.project_path.setText(text)

    def set_recent_projects(self, paths: list[str]) -> None:
        current = self.recent_projects.currentText()
        self.recent_projects.blockSignals(True)
        self.recent_projects.clear()
        self.recent_projects.addItems(list(dict.fromkeys(path for path in paths if path)))
        self.recent_projects.setCurrentText(current)
        self.recent_projects.blockSignals(False)

    def set_job(self, job: Job, busy: bool = False) -> None:
        self.job = job
        self.job_label.setText(f"{job.name} ({job.job_id})")
        self.fbx_label.setText(job.unity_input_path or "없음 — FBX 없이도 Unity 채팅을 사용할 수 있습니다.")
        self.asset_label.setText(job.unity_asset_path or "가져온 Asset 없음")
        if job.unity_project_path and not self.project_path.text().strip():
            self.project_path.setText(job.unity_project_path)
        self.import_button.setEnabled(bool(job.unity_input_path) and not busy)
        self.include_asset.setEnabled(bool(job.unity_asset_path))
        if not job.unity_asset_path:
            self.include_asset.setChecked(False)
        self._latest_turn = job.latest_unity_turn
        self._render_history(job)
        self._render_review(self._latest_turn)

    def _render_history(self, job: Job) -> None:
        lines: list[str] = []
        for turn in job.unity_turns:
            lines.append(f"\n[사용자 · {turn.turn_id}]\n{turn.user_text}")
            if turn.assistant_text:
                lines.append(f"\n[로컬 모델]\n{turn.assistant_text}")
            lines.append(
                f"\n[상태] Agent={turn.status} · 자동={turn.automated_status} · "
                f"인간={turn.human_review_status}"
            )
        self.chat.setPlainText("\n".join(lines).strip())
        self.chat.moveCursor(self.chat.textCursor().MoveOperation.End)

    def set_session(self, session: UnitySession | None) -> None:
        self.session = session
        status = session.status if session else "disconnected"
        self.agent_status.setText(f"Unity Agent: {status}")
        ready = bool(session and session.status == "ready")
        self.mcp_status.setText(f"Unity MCP: {'연결됨' if ready else '확인 전'}")
        self.bridge_status.setText(f"Editor Bridge: {'연결됨' if ready else '확인 전'}")
        if session and session.project_identity:
            identity = session.project_identity
            self.identity_status.setText(
                f"실제 프로젝트: {identity.get('productName') or '?'} · "
                f"{identity.get('projectPath') or '?'}"
            )
        elif session and session.error:
            self.identity_status.setText(f"identity 오류: {session.error}")
        else:
            self.identity_status.setText("실제 프로젝트 identity: 확인 전")
        self.connect_button.setEnabled(not ready)
        self.disconnect_button.setEnabled(session is not None and status in {"starting", "ready"})
        self.send_button.setEnabled(ready)
        self.cancel_button.setEnabled(ready)

    def append_event(self, event: dict) -> None:
        event_type = event.get("type")
        if event_type == "assistant_text":
            self.chat.moveCursor(self.chat.textCursor().MoveOperation.End)
            self.chat.insertPlainText(str(event.get("text") or ""))
        elif event_type == "turn_started":
            self.chat.appendPlainText(f"\n[실행 시작 · {event.get('id')}]")
        elif event_type == "tool_started":
            args = json.dumps(event.get("arguments", {}), ensure_ascii=False)
            self.chat.appendPlainText(f"[도구 시작] {event.get('tool')} {args}")
        elif event_type == "tool_finished":
            mark = "완료" if event.get("success") else "실패"
            self.chat.appendPlainText(f"[도구 {mark}] {event.get('tool')}")
        elif event_type == "milestone":
            self.chat.appendPlainText(
                f"[마일스톤 {event.get('index')}/{event.get('total')}] {event.get('message')}"
            )
        elif event_type in {"warning", "protocol_error", "session_error", "turn_failed"}:
            self.chat.appendPlainText(f"[오류/경고] {event.get('error') or event.get('message')}")
        elif event_type == "screenshot":
            path = str(event.get("path") or "")
            self.chat.appendPlainText(f"[스크린샷] {path}")
            self._show_screenshot(path)
        elif event_type == "vision_analysis":
            self.chat.appendPlainText(
                f"[{event.get('label', 'AI 정적 분석 — 참고용')}]\n{event.get('text', '')}\n"
                "정적 분석은 동적 품질의 인간 검토를 대체하지 않습니다."
            )

    def _show_screenshot(self, path: str) -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.screenshot_preview.setText(path)
            return
        self.screenshot_preview.setPixmap(
            pixmap.scaled(
                self.screenshot_preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _render_review(self, turn: UnityTurn | None) -> None:
        self._latest_turn = turn
        if turn is None:
            self.execution_result.setText("Agent execution: 대기")
            self.automated_result.setText("Automated verification: unavailable")
            self.human_result.setText("Human review: pending")
            self.checks.clear()
            for button in (self.accept_button, self.reject_button, self.repair_button):
                button.setEnabled(False)
            return
        self.execution_result.setText(f"Agent execution: {turn.status}")
        self.automated_result.setText(f"Automated verification: {turn.automated_status}")
        self.human_result.setText(f"Human review: {turn.human_review_status}")
        self.checks.setPlainText(
            json.dumps(
                {
                    "requested": turn.requested_checks,
                    "measured": turn.measured_checks,
                    "skipped": turn.skipped_checks,
                    "unmapped": turn.unmapped_requirements,
                }, ensure_ascii=False, indent=2,
            )
        )
        if not turn.requested_checks or not turn.measured_checks:
            self.automated_result.setText(
                f"Automated verification: {turn.automated_status} — 자동 검증 항목이 없거나 "
                "불완전합니다. 실행 결과를 Unity에서 직접 확인하세요."
            )
        pending = turn.status == "succeeded" and turn.human_review_status == "pending"
        self.accept_button.setEnabled(pending)
        self.reject_button.setEnabled(pending)
        self.repair_button.setEnabled(
            turn.human_review_status == "rejected" or turn.status == "failed"
        )
        self.review_note.setPlainText(turn.human_review_note)
        if turn.screenshot_paths:
            self._show_screenshot(turn.screenshot_paths[-1])

    def _send(self) -> None:
        text = self.input.toPlainText()
        if text.strip():
            self.send_requested.emit(
                text, self.include_asset.isChecked(), self.include_scene.isChecked(),
                self.analyze_screenshot.isChecked(),
            )
            self.input.clear()

    def _accept(self) -> None:
        if self._latest_turn:
            self.accept_requested.emit(self._latest_turn.turn_id, self.review_note.toPlainText())

    def _reject(self) -> None:
        if self._latest_turn:
            self.reject_requested.emit(self._latest_turn.turn_id, self.review_note.toPlainText())

    def _repair(self) -> None:
        if self._latest_turn:
            self.repair_requested.emit(
                self._latest_turn.turn_id, self.review_note.toPlainText(),
                self.include_asset.isChecked(), self.include_scene.isChecked(),
                self.analyze_screenshot.isChecked(),
            )

    def _open_latest(self, kind: str) -> None:
        turn = self._latest_turn
        if not turn:
            return
        if kind == "screenshot":
            path = turn.screenshot_paths[-1] if turn.screenshot_paths else None
        elif kind == "receipt":
            path = turn.receipt_path
        elif kind == "scene":
            path = self.job.latest_unity_scene_path if self.job else None
            if path and not Path(path).is_absolute():
                path = str(Path(self.project_path.text()) / Path(path.replace("/", "\\")))
        else:
            path = turn.run_log_path or turn.jsonl_log_path
        if path:
            self.open_path_requested.emit(path)

    def choose_project(self) -> str:
        selected = QFileDialog.getExistingDirectory(self, "Unity 프로젝트 선택", self.project_path.text())
        if selected:
            self.project_path.setText(selected)
        return selected
