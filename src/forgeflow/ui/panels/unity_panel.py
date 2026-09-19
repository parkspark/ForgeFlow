from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from forgeflow.domain.job import Job, UnitySession, UnityTurn

from ..status import status_text


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
        self.setObjectName("unityPanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(5)
        self.main_splitter.addWidget(self._build_chat())
        self.main_splitter.addWidget(self._build_sidebar())
        self.main_splitter.setStretchFactor(0, 5)
        self.main_splitter.setStretchFactor(1, 2)
        self.main_splitter.setSizes([820, 380])
        root.addWidget(self.main_splitter, 1)
        self.set_session(None)

    def _build_chat(self) -> QGroupBox:
        # The conversation is the primary workspace and always receives most
        # of the available width and height.
        chat_group = QGroupBox("Unity 텍스트 채팅")
        chat_group.setObjectName("unityChatGroup")
        chat_layout = QVBoxLayout(chat_group)
        chat_layout.setContentsMargins(12, 16, 12, 12)
        chat_layout.setSpacing(8)
        chat_header = QHBoxLayout()
        chat_description = QLabel("로컬 모델과 Unity 도구 실행 내역을 한곳에서 확인합니다.")
        chat_description.setObjectName("unityChatDescription")
        chat_header.addWidget(chat_description, 1)
        self.chat_connection_hint = QLabel("프로젝트 연결 대기")
        self.chat_connection_hint.setProperty("connectionState", "idle")
        chat_header.addWidget(self.chat_connection_hint)
        self.sidebar_toggle = QPushButton("보조 패널 숨기기")
        self.sidebar_toggle.setCheckable(True)
        self.sidebar_toggle.setToolTip("연결·작업 컨텍스트·검토 패널을 접어 채팅을 넓게 봅니다.")
        self.sidebar_toggle.toggled.connect(self._toggle_sidebar)
        chat_header.addWidget(self.sidebar_toggle)
        chat_layout.addLayout(chat_header)

        self.chat = QPlainTextEdit()
        self.chat.setObjectName("unityChatHistory")
        self.chat.setReadOnly(True)
        self.chat.setMinimumHeight(340)
        self.chat.setPlaceholderText(
            "Unity 프로젝트를 연결하면 로컬 모델 응답, 도구 호출, 마일스톤과 오류가 여기에 표시됩니다."
        )
        chat_layout.addWidget(self.chat, 1)

        self.screenshot_preview = QLabel("스크린샷 없음")
        self.screenshot_preview.setObjectName("previewFrame")
        self.screenshot_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.screenshot_preview.setMinimumHeight(80)
        self.screenshot_preview.setMaximumHeight(180)
        self.screenshot_preview.setVisible(False)
        chat_layout.addWidget(self.screenshot_preview)
        chat_layout.addWidget(self._build_composer())
        return chat_group

    def _build_composer(self) -> QWidget:
        composer = QWidget()
        composer.setObjectName("unityComposer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(10, 10, 10, 10)
        composer_layout.setSpacing(8)
        self.input = QTextEdit()
        self.input.setObjectName("unityPromptInput")
        self.input.setAccessibleName("Unity 요청 입력")
        self.input.setPlaceholderText(
            "Unity에 요청할 내용을 입력하세요. 예: 현재 활성 씬과 주요 GameObject를 분석해줘.\n"
            "Ctrl+Enter로 전송"
        )
        self.input.setMinimumHeight(82)
        self.input.setMaximumHeight(120)
        composer_layout.addWidget(self.input)
        options = QHBoxLayout()
        options.setSpacing(12)
        self.include_scene = QCheckBox("씬 컨텍스트")
        self.include_scene.setToolTip("현재 Unity 씬 정보를 요청에 포함합니다.")
        self.include_scene.setChecked(True)
        self.include_asset = QCheckBox("FBX 컨텍스트")
        self.include_asset.setToolTip("현재 가져온 Humanoid FBX 정보를 요청에 포함합니다.")
        self.analyze_screenshot = QCheckBox("스크린샷 자동 분석")
        self.analyze_screenshot.setToolTip(
            "실행 후 캡처된 정적 화면을 로컬 모델이 함께 분석합니다."
        )
        options.addWidget(self.include_scene)
        options.addWidget(self.include_asset)
        options.addWidget(self.analyze_screenshot)
        options.addStretch()
        self.cancel_button = QPushButton("실행 취소")
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.send_button = QPushButton("전송  Ctrl+Enter")
        self.send_button.setProperty("actionRole", "primary")
        self.send_button.setMinimumWidth(126)
        self.send_button.clicked.connect(self._send)
        options.addWidget(self.cancel_button)
        options.addWidget(self.send_button)
        composer_layout.addLayout(options)
        self.send_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.input)
        self.send_shortcut.activated.connect(self._send)
        self.send_keypad_shortcut = QShortcut(QKeySequence("Ctrl+Enter"), self.input)
        self.send_keypad_shortcut.activated.connect(self._send)
        return composer

    def _build_sidebar(self) -> QScrollArea:
        # Connection, asset context and review are secondary. They remain
        # reachable in a dedicated scroll rail without reducing chat height.
        self.sidebar_scroll = QScrollArea()
        self.sidebar_scroll.setObjectName("unitySidebar")
        self.sidebar_scroll.setWidgetResizable(True)
        self.sidebar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.sidebar_scroll.setMinimumWidth(340)
        self.sidebar_scroll.setMaximumWidth(460)
        sidebar_body = QWidget()
        sidebar_body.setObjectName("unitySidebarBody")
        sidebar_body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        sidebar_layout = QVBoxLayout(sidebar_body)
        sidebar_layout.setContentsMargins(4, 0, 4, 4)
        sidebar_layout.setSpacing(10)
        sidebar_layout.addWidget(self._build_connection())
        sidebar_layout.addWidget(self._build_job_context())
        sidebar_layout.addWidget(self._build_review())

        guidance = QLabel(
            "Unity Play Mode에서 입력·애니메이션·카메라·물리·타이밍을 직접 확인한 뒤 승인하세요."
        )
        guidance.setObjectName("guidance")
        self._make_wrapping_label_shrinkable(guidance)
        sidebar_layout.addWidget(guidance)
        sidebar_layout.addLayout(self._build_review_actions())
        sidebar_layout.addStretch()

        self.sidebar_scroll.setWidget(sidebar_body)
        return self.sidebar_scroll

    def _build_connection(self) -> QGroupBox:
        connection = QGroupBox("Unity 연결")
        connection_layout = QVBoxLayout(connection)
        connection_layout.setSpacing(8)
        project_caption = QLabel("프로젝트 경로")
        project_caption.setProperty("sectionCaption", True)
        connection_layout.addWidget(project_caption)
        self.project_path = QLineEdit()
        self.project_path.setPlaceholderText(r"C:\UnityProjects\MyGame")
        self.project_path.setClearButtonEnabled(True)
        connection_layout.addWidget(self.project_path)
        project_row = QHBoxLayout()
        self.recent_projects = QComboBox()
        self.recent_projects.setPlaceholderText("최근 프로젝트")
        self.recent_projects.currentTextChanged.connect(self._recent_selected)
        project_row.addWidget(self.recent_projects, 1)
        browse = QPushButton("찾아보기")
        browse.clicked.connect(self.browse_project_requested)
        project_row.addWidget(browse)
        connection_layout.addLayout(project_row)
        connection_actions = QGridLayout()
        self.connect_button = QPushButton("연결")
        self.connect_button.setProperty("actionRole", "primary")
        self.connect_button.clicked.connect(
            lambda: self.connect_requested.emit(self.project_path.text().strip())
        )
        self.disconnect_button = QPushButton("연결 해제")
        self.disconnect_button.clicked.connect(self.disconnect_requested)
        self.open_project_button = QPushButton("프로젝트 폴더 열기")
        self.open_project_button.clicked.connect(self.open_project_requested)
        connection_actions.addWidget(self.connect_button, 0, 0)
        connection_actions.addWidget(self.disconnect_button, 0, 1)
        connection_actions.addWidget(self.open_project_button, 1, 0, 1, 2)
        connection_layout.addLayout(connection_actions)

        self.agent_status = QLabel("Unity Agent: 연결 안 됨")
        self.mcp_status = QLabel("Unity MCP: 확인 전")
        self.bridge_status = QLabel("Editor Bridge: 확인 전")
        self.identity_status = QLabel("실제 프로젝트 identity: 확인 전")
        for widget in (
            self.agent_status,
            self.mcp_status,
            self.bridge_status,
            self.identity_status,
        ):
            self._make_wrapping_label_shrinkable(widget)
            widget.setProperty("connectionState", "idle")
            connection_layout.addWidget(widget)
        self.restart_notice = QLabel(
            "새 로컬 모델 세션 — Unity 프로젝트 상태와 저장된 채팅 기록은 유지되지만 "
            "모델의 내부 대화 컨텍스트는 초기화되었습니다."
        )
        self.restart_notice.setObjectName("guidance")
        self._make_wrapping_label_shrinkable(self.restart_notice)
        connection_layout.addWidget(self.restart_notice)
        return connection

    def _build_job_context(self) -> QGroupBox:
        context = QGroupBox("작업 컨텍스트")
        context_layout = QVBoxLayout(context)
        context_layout.setSpacing(6)
        job_caption = QLabel("ForgeFlow Job")
        job_caption.setProperty("sectionCaption", True)
        context_layout.addWidget(job_caption)
        self.job_label = QLabel("선택된 Job 없음")
        self._make_wrapping_label_shrinkable(self.job_label)
        context_layout.addWidget(self.job_label)
        fbx_caption = QLabel("Humanoid FBX")
        fbx_caption.setProperty("sectionCaption", True)
        context_layout.addWidget(fbx_caption)
        self.fbx_label = QLabel("없음 — FBX 없이도 Unity 채팅을 사용할 수 있습니다.")
        self._make_wrapping_label_shrinkable(self.fbx_label)
        self.fbx_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        context_layout.addWidget(self.fbx_label)
        self.import_button = QPushButton("Unity 프로젝트로 가져오기")
        self.import_button.clicked.connect(self.import_fbx_requested)
        context_layout.addWidget(self.import_button)
        asset_caption = QLabel("Unity Asset")
        asset_caption.setProperty("sectionCaption", True)
        context_layout.addWidget(asset_caption)
        self.asset_label = QLabel("가져온 Asset 없음")
        self._make_wrapping_label_shrinkable(self.asset_label)
        self.asset_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        context_layout.addWidget(self.asset_label)
        return context

    def _build_review(self) -> QGroupBox:
        review = QGroupBox("실행 결과 검토")
        review_form = QFormLayout(review)
        review_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.execution_result = QLabel("실행 결과: 대기")
        self.automated_result = QLabel("자동 검증: 검증 정보 없음")
        self.human_result = QLabel("사용자 검토: 대기")
        for result_label in (self.execution_result, self.automated_result, self.human_result):
            self._make_wrapping_label_shrinkable(result_label)
        self.checks = QPlainTextEdit()
        self.checks.setReadOnly(True)
        self.checks.setMaximumHeight(130)
        self.review_note = QTextEdit()
        self.review_note.setPlaceholderText("검토 메모 또는 수정 요청을 입력하세요.")
        self.review_note.setMinimumHeight(76)
        self.review_note.setMaximumHeight(110)
        review_form.addRow("에이전트", self.execution_result)
        review_form.addRow("자동 검증", self.automated_result)
        review_form.addRow("인간 검토", self.human_result)
        review_form.addRow("검증 상세", self.checks)
        review_form.addRow("검토 메모", self.review_note)
        return review

    def _build_review_actions(self) -> QGridLayout:
        actions = QGridLayout()
        actions.setHorizontalSpacing(6)
        actions.setVerticalSpacing(6)
        self.accept_button = QPushButton("결과 승인")
        self.accept_button.setProperty("actionRole", "primary")
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
        actions.addWidget(self.accept_button, 0, 0)
        actions.addWidget(self.reject_button, 0, 1)
        actions.addWidget(self.repair_button, 1, 0, 1, 2)
        actions.addWidget(self.focus_button, 2, 0)
        actions.addWidget(self.screenshot_button, 2, 1)
        actions.addWidget(self.scene_button, 3, 0)
        actions.addWidget(self.receipt_button, 3, 1)
        actions.addWidget(self.log_button, 4, 0, 1, 2)
        return actions

    @staticmethod
    def _make_wrapping_label_shrinkable(label: QLabel) -> None:
        """Let long, unbroken paths wrap inside the narrow sidebar."""
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def _toggle_sidebar(self, hidden: bool) -> None:
        self.sidebar_scroll.setVisible(not hidden)
        self.sidebar_toggle.setText("보조 패널 보기" if hidden else "보조 패널 숨기기")

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
        self.fbx_label.setText(
            job.unity_input_path or "없음 — FBX 없이도 Unity 채팅을 사용할 수 있습니다."
        )
        self.asset_label.setText(job.unity_asset_path or "가져온 Asset 없음")
        self.fbx_label.setToolTip(job.unity_input_path or "")
        self.asset_label.setToolTip(job.unity_asset_path or "")
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
                f"\n[상태] Agent={status_text(turn.status)} · 자동={status_text(turn.automated_status)} · "
                f"인간={status_text(turn.human_review_status)}"
            )
        self.chat.setPlainText("\n".join(lines).strip())
        self.chat.moveCursor(self.chat.textCursor().MoveOperation.End)

    def set_session(self, session: UnitySession | None) -> None:
        self.session = session
        status = session.status if session else "disconnected"
        ready = bool(session and session.status == "ready")
        failed = status == "failed"
        state = "ok" if ready else ("error" if failed else "idle")
        status_text = {
            "disconnected": "연결 안 됨",
            "starting": "연결 중…",
            "ready": "연결됨",
            "closed": "연결 종료",
            "failed": "연결 실패",
        }.get(status, status)
        self._set_connection_label(self.agent_status, f"Unity Agent  ·  {status_text}", state)
        dependent_text = "연결됨" if ready else ("연결 실패" if failed else "확인 전")
        self._set_connection_label(self.mcp_status, f"Unity MCP  ·  {dependent_text}", state)
        self._set_connection_label(self.bridge_status, f"Editor Bridge  ·  {dependent_text}", state)
        if session and session.project_identity:
            identity = session.project_identity
            self._set_connection_label(
                self.identity_status,
                f"실제 프로젝트: {identity.get('productName') or '?'} · "
                f"{identity.get('projectPath') or '?'}",
                "ok",
            )
        elif session and session.error:
            self._set_connection_label(
                self.identity_status, f"프로젝트 identity 오류: {session.error}", "error"
            )
        else:
            self._set_connection_label(
                self.identity_status, "실제 프로젝트 identity  ·  확인 전", "idle"
            )
        if ready and session and session.project_identity:
            product = session.project_identity.get("productName") or "Unity"
            hint = f"연결됨  ·  {product}"
        elif status == "starting":
            hint = "Unity 연결 중…"
        elif failed:
            hint = "연결 실패  ·  우측 상태 확인"
        else:
            hint = "프로젝트 연결 대기"
        self._set_connection_label(self.chat_connection_hint, hint, state)
        self.restart_notice.setVisible(session is not None and status in {"starting", "ready"})
        self.connect_button.setEnabled(status in {"disconnected", "closed", "failed"})
        self.disconnect_button.setEnabled(session is not None and status in {"starting", "ready"})
        self.send_button.setEnabled(ready)
        self.cancel_button.setEnabled(ready)

    @staticmethod
    def _set_connection_label(label: QLabel, text: str, state: str) -> None:
        label.setText(text)
        label.setProperty("connectionState", state)
        label.style().unpolish(label)
        label.style().polish(label)
        label.update()

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
        self.screenshot_preview.setVisible(True)
        if pixmap.isNull():
            self.screenshot_preview.setText(path)
            return
        self.screenshot_preview.setPixmap(
            pixmap.scaled(
                self.screenshot_preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _render_review(self, turn: UnityTurn | None) -> None:
        self._latest_turn = turn
        if turn is None:
            self.execution_result.setText("실행 결과: 대기")
            self.automated_result.setText("자동 검증: 검증 정보 없음")
            self.human_result.setText("사용자 검토: 대기")
            self.checks.clear()
            self.screenshot_preview.clear()
            self.screenshot_preview.setText("스크린샷 없음")
            self.screenshot_preview.setVisible(False)
            for button in (self.accept_button, self.reject_button, self.repair_button):
                button.setEnabled(False)
            return
        self.execution_result.setText(f"실행 결과: {status_text(turn.status)}")
        self.automated_result.setText(f"자동 검증: {status_text(turn.automated_status)}")
        self.human_result.setText(f"사용자 검토: {status_text(turn.human_review_status)}")
        self.checks.setPlainText(
            json.dumps(
                {
                    "requested": turn.requested_checks,
                    "measured": turn.measured_checks,
                    "skipped": turn.skipped_checks,
                    "unmapped": turn.unmapped_requirements,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if not turn.requested_checks or not turn.measured_checks:
            self.automated_result.setText(
                f"자동 검증: {status_text(turn.automated_status)} — 자동 검증 항목이 없거나 "
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
        else:
            self.screenshot_preview.clear()
            self.screenshot_preview.setText("스크린샷 없음")
            self.screenshot_preview.setVisible(False)

    def _send(self) -> None:
        text = self.input.toPlainText()
        if text.strip():
            self.send_requested.emit(
                text,
                self.include_asset.isChecked(),
                self.include_scene.isChecked(),
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
                self._latest_turn.turn_id,
                self.review_note.toPlainText(),
                self.include_asset.isChecked(),
                self.include_scene.isChecked(),
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
        selected = QFileDialog.getExistingDirectory(
            self, "Unity 프로젝트 선택", self.project_path.text()
        )
        if selected:
            self.project_path.setText(selected)
        return selected
