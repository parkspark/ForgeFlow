from __future__ import annotations

import ctypes
import os
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from forgeflow.adapters.blender_adapter import BlenderAdapter
from forgeflow.adapters.modeling_adapter import ModelingAdapter
from forgeflow.adapters.rigging_adapter import RiggingAdapter
from forgeflow.adapters.unity_adapter import UnityAdapter
from forgeflow.config import AppConfig
from forgeflow.domain.job import Job
from forgeflow.services.environment_service import EnvironmentWorker
from forgeflow.services.job_service import JobService
from forgeflow.services.pipeline_service import PipelineService

from .dialogs.settings_dialog import SettingsDialog
from .panels.blender_panel import BlenderPanel
from .panels.log_panel import LogPanel
from .panels.modeling_panel import ModelingPanel
from .panels.next_step_panel import NextStepPanel
from .panels.progress_panel import ProgressPanel
from .panels.project_panel import ProjectPanel
from .panels.rigging_panel import RiggingPanel
from .panels.unity_panel import UnityPanel
from .status import set_status_badge
from .theme import build_stylesheet, normalize_theme


class WorkspaceTitle(QLabel):
    """Keep the selected job identifiable when the sidebar is filtered or narrow."""

    def __init__(self):
        super().__init__()
        self.full_text = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setObjectName("workspaceTitle")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_title(self, text: str) -> None:
        self.full_text = text
        self.setToolTip(text)
        self.setAccessibleName(text)
        self._elide()

    def _elide(self) -> None:
        self.setText(
            self.fontMetrics().elidedText(
                self.full_text, Qt.TextElideMode.ElideRight, max(0, self.width())
            )
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig | None = None, *, run_environment_checks: bool = True):
        super().__init__()
        self.config = config or AppConfig.load()
        self.saved_config = self.config
        self.jobs = JobService(self.config.jobs_root)
        self.job_list = self.jobs.recover_interrupted()
        self.current_job: Job | None = None
        self.pipeline = PipelineService(
            self.jobs,
            ModelingAdapter(self.config, self.jobs),
            BlenderAdapter(self.config, self.jobs),
            RiggingAdapter(self.config, self.jobs),
            self,
        )
        self.unity = UnityAdapter(self.config, self.jobs, self)
        self.environment_worker: EnvironmentWorker | None = None
        self._build_ui()
        self._connect()
        self.project_panel.set_jobs(self.job_list)
        if self.job_list:
            self.select_job(self.job_list[0].job_id)
        if run_environment_checks:
            self.refresh_environment()
        else:
            self.refresh_environment_button.setEnabled(True)

    def _build_ui(self) -> None:
        self.setWindowTitle("ForgeFlow - 이미지, 3d 모델링, Blender, Unity 통합 워크플로")
        available = self.screen().availableGeometry()
        self.resize(min(1480, available.width() - 40), min(940, available.height() - 80))
        central = QWidget()
        root = QVBoxLayout(central)
        environment_box = QGroupBox("환경 연결 상태")
        env_root = QVBoxLayout(environment_box)
        env_layout = QHBoxLayout()
        env_root.addLayout(env_layout)
        self.environment_summary = QLabel("환경 확인 전")
        self.environment_summary.setProperty("envState", "checking")
        env_layout.addWidget(self.environment_summary)
        self.environment_toggle = QPushButton("상세 보기")
        self.environment_toggle.setCheckable(True)
        env_layout.addWidget(self.environment_toggle)
        self.environment_details = QScrollArea()
        self.environment_details.setWidgetResizable(True)
        self.environment_details.setFrameShape(QFrame.Shape.NoFrame)
        detail_body = QWidget()
        detail_layout = QVBoxLayout(detail_body)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        connections = QGridLayout()
        detail_layout.addLayout(connections)
        self.environment_details.hide()
        self.environment_toggle.toggled.connect(self._toggle_environment_details)
        self.environment_labels = {}
        for key, label in (
            ("modeling", "Pixal3D"),
            ("gpu", "GPU"),
            ("wsl", "WSL"),
            ("mcp", "Blender MCP"),
            ("unirig", "UniRig"),
            ("blender", "Blender"),
            ("unity_mcp", "Unity MCP"),
        ):
            widget = QLabel(f"● {label}: 확인 중")
            widget.setProperty("envState", "checking")
            self.environment_labels[key] = widget
            index = len(self.environment_labels) - 1
            connections.addWidget(widget, index // 4, index % 4)
        env_layout.addStretch()
        env_layout.addWidget(QLabel("테마"))
        self.theme_selector = QComboBox()
        self.theme_selector.setMinimumWidth(92)
        self.theme_selector.addItem("다크", "dark")
        self.theme_selector.addItem("라이트", "light")
        selected_theme = normalize_theme(self.config.theme)
        self.theme_selector.setCurrentIndex(self.theme_selector.findData(selected_theme))
        env_layout.addWidget(self.theme_selector)
        self.refresh_environment_button = QPushButton("새로고침")
        env_layout.addWidget(self.refresh_environment_button)

        model_layout = QHBoxLayout()
        detail_layout.addLayout(model_layout)
        for key, title, model in (
            ("ollama_blender", "Blender 모델", self.config.ollama_model),
            ("ollama_unity", "Unity 모델", self.config.unity_agent_model),
        ):
            widget = QLabel(f"● {title}: 확인 중\n{model}")
            widget.setWordWrap(True)
            widget.setProperty("envState", "checking")
            self.environment_labels[key] = widget
            model_layout.addWidget(widget, 1)
        self.environment_details.setWidget(detail_body)
        detail_body.setAutoFillBackground(False)
        self.environment_details.viewport().setAutoFillBackground(False)
        env_root.addWidget(self.environment_details)
        root.addWidget(environment_box)
        splitter = QSplitter()
        self.project_panel = ProjectPanel()
        self.project_panel.setMinimumWidth(280)
        splitter.addWidget(self.project_panel)
        self.tabs = QTabWidget()
        self.modeling_panel = ModelingPanel()
        self.blender_panel = BlenderPanel()
        self.rigging_panel = RiggingPanel()
        self.unity_panel = UnityPanel()
        self.log_panel = LogPanel()
        self.next_steps = {}
        for key, panel, actions in (
            (
                "modeling",
                self.modeling_panel,
                [("blender", "Blender에서 편집"), ("rigging", "바로 리깅으로 이동")],
            ),
            ("blender", self.blender_panel, [("rigging", "리깅으로 이동")]),
            ("rigging", self.rigging_panel, [("unity", "Unity에서 사용")]),
        ):
            card = NextStepPanel(actions)
            panel.layout().addWidget(card)
            self.next_steps[key] = card
            for target, button in card.buttons.items():
                button.clicked.connect(
                    lambda checked=False, destination=target: self.go_to_next_step(destination)
                )
        # A tab's content may be taller or wider than a laptop's usable area.
        # Keep the tab bar and running-operation controls outside the scroll
        # viewport so expanded details never force the window off screen.
        self.tab_pages = {}
        for panel, title in (
            (self.modeling_panel, "1. 이미지 → 3D"),
            (self.blender_panel, "2. Blender 자연어 편집"),
            (self.rigging_panel, "3. Humanoid 리깅"),
            (self.unity_panel, "4. Unity 텍스트 컨트롤"),
            (self.log_panel, "실행 로그"),
        ):
            page = QScrollArea()
            page.setWidgetResizable(True)
            page.setFrameShape(QFrame.Shape.NoFrame)
            page.setWidget(panel)
            # QScrollArea enables the child's palette fill by default, which
            # otherwise paints a light background beneath the dark theme.
            panel.setAutoFillBackground(False)
            page.viewport().setAutoFillBackground(False)
            self.tab_pages[panel] = page
            self.tabs.addTab(page, title)
        work_area = QWidget()
        work_layout = QVBoxLayout(work_area)
        work_layout.setContentsMargins(0, 0, 0, 0)
        self.workspace_header = QWidget()
        header_layout = QHBoxLayout(self.workspace_header)
        header_layout.setContentsMargins(10, 0, 6, 0)
        self.workspace_title = WorkspaceTitle()
        self.workspace_status = QLabel()
        header_layout.addWidget(self.workspace_title, 1)
        header_layout.addWidget(self.workspace_status)
        work_layout.addWidget(self.workspace_header)
        self.workspace_header.hide()
        self.workspace_stack = QStackedWidget()
        self.welcome = QWidget()
        welcome_layout = QVBoxLayout(self.welcome)
        welcome_layout.setContentsMargins(32, 24, 32, 24)
        welcome_layout.addStretch()
        welcome_title = QLabel("이미지 한 장으로 3D 작업을 시작하세요")
        welcome_title.setObjectName("welcomeTitle")
        welcome_title.setWordWrap(True)
        welcome_layout.addWidget(welcome_title)
        description = QLabel(
            "PNG 또는 JPG 이미지를 선택하면 원본을 복사해 새 작업을 만듭니다.\n"
            "생성 결과와 편집 이력은 작업별로 보관됩니다."
        )
        description.setWordWrap(True)
        welcome_layout.addWidget(description)
        steps = QLabel("이미지 → 3D 모델 생성 → Blender 편집 → Humanoid 리깅 → Unity")
        steps.setWordWrap(True)
        steps.setProperty("sectionCaption", "true")
        welcome_layout.addWidget(steps)
        self.welcome_create = QPushButton("이미지로 새 작업 만들기")
        self.welcome_create.setProperty("actionRole", "primary")
        self.welcome_create.clicked.connect(self.create_job)
        welcome_layout.addWidget(self.welcome_create, 0, Qt.AlignmentFlag.AlignLeft)
        self.welcome_settings = QPushButton("실행 환경 설정")
        self.welcome_settings.clicked.connect(self.edit_settings)
        welcome_layout.addWidget(self.welcome_settings, 0, Qt.AlignmentFlag.AlignLeft)
        welcome_layout.addStretch()
        self.welcome_page = QScrollArea()
        self.welcome_page.setWidgetResizable(True)
        self.welcome_page.setFrameShape(QFrame.Shape.NoFrame)
        self.welcome_page.setWidget(self.welcome)
        self.welcome.setAutoFillBackground(False)
        self.welcome_page.viewport().setAutoFillBackground(False)
        self.workspace_stack.addWidget(self.welcome_page)
        self.workspace_stack.addWidget(self.tabs)
        work_layout.addWidget(self.workspace_stack, 1)
        self.progress_panel = ProgressPanel()
        self.cancel_button = self.progress_panel.cancel
        work_layout.addWidget(self.progress_panel)
        splitter.addWidget(work_area)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("준비됨")
        self._apply_theme(selected_theme)
        self._update_detail_heights()

    def _show_panel(self, panel: QWidget) -> None:
        self.tabs.setCurrentWidget(self.tab_pages[panel])

    def _update_detail_heights(self) -> None:
        # Reserve room for the working tab on short/high-DPI screens. Details
        # can scroll independently while the operation/cancel row stays fixed.
        detail_height = max(44, (self.height() - 440) // 2)
        self.environment_details.setMaximumHeight(min(140, detail_height))
        self.progress_panel.log.setMaximumHeight(min(180, detail_height))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "progress_panel"):
            self._update_detail_heights()

    def _connect(self) -> None:
        self.project_panel.new_requested.connect(self.create_job)
        self.project_panel.job_selected.connect(self.select_job)
        self.project_panel.settings_requested.connect(self.edit_settings)
        self.modeling_panel.generate_requested.connect(self.start_modeling)
        self.modeling_panel.retry_requested.connect(self.start_modeling)
        self.modeling_panel.open_file_requested.connect(self.open_path)
        self.modeling_panel.open_folder_requested.connect(self.open_job_folder)
        self.blender_panel.inspect_requested.connect(self.inspect_scene)
        self.blender_panel.propose_requested.connect(self.propose_blender)
        self.blender_panel.approve_requested.connect(self.approve_blender)
        self.blender_panel.deny_requested.connect(self.deny_blender)
        self.blender_panel.open_file_requested.connect(self.open_path)
        self.rigging_panel.run_requested.connect(self.start_rigging)
        self.rigging_panel.open_file_requested.connect(self.open_path)
        self.rigging_panel.open_folder_requested.connect(self.open_path)
        self.unity_panel.browse_project_requested.connect(self.choose_unity_project)
        self.unity_panel.connect_requested.connect(self.connect_unity)
        self.unity_panel.disconnect_requested.connect(self.disconnect_unity)
        self.unity_panel.open_project_requested.connect(self.open_unity_project)
        self.unity_panel.import_fbx_requested.connect(self.import_unity_fbx)
        self.unity_panel.send_requested.connect(self.send_unity_prompt)
        self.unity_panel.cancel_requested.connect(self.cancel_unity)
        self.unity_panel.accept_requested.connect(self.accept_unity_review)
        self.unity_panel.reject_requested.connect(self.reject_unity_review)
        self.unity_panel.repair_requested.connect(self.repair_unity_turn)
        self.unity_panel.open_path_requested.connect(self.open_path)
        self.unity_panel.focus_unity_requested.connect(self.focus_unity)
        self.unity.event_received.connect(self._unity_event)
        self.unity.session_changed.connect(self._unity_session_changed)
        self.unity.job_changed.connect(self._job_changed)
        self.unity.protocol_error.connect(self._unity_error)
        self.unity.log_received.connect(lambda line: self.log_panel.append(f"[Unity Agent] {line}"))
        self.pipeline.log_received.connect(self.log_panel.append)
        self.pipeline.log_received.connect(self.progress_panel.append)
        self.pipeline.event_received.connect(self.progress_panel.on_event)
        self.pipeline.operation_finished.connect(self.progress_panel.finish)
        self.progress_panel.cancel_requested.connect(self.pipeline.cancel_active)
        self.pipeline.log_received.connect(self._route_rigging_log)
        self.pipeline.job_changed.connect(self._job_changed)
        self.pipeline.inspect_ready.connect(self._inspection_ready)
        self.pipeline.plan_ready.connect(self._plan_ready)
        self.pipeline.operation_finished.connect(self._operation_finished)
        self.refresh_environment_button.clicked.connect(self.refresh_environment)
        self.theme_selector.currentIndexChanged.connect(self._theme_changed)

    def _apply_theme(self, name: str) -> None:
        self.setStyleSheet(build_stylesheet(name))

    def _theme_changed(self, index: int) -> None:
        name = normalize_theme(str(self.theme_selector.itemData(index)))
        self._apply_theme(name)
        if name == self.config.theme:
            return
        self.config = replace(self.config, theme=name)
        try:
            updated = replace(self.saved_config, theme=name)
            updated.save()
            self.saved_config = updated
            self.statusBar().showMessage(
                f"{'라이트' if name == 'light' else '다크'} 테마를 적용하고 저장했습니다.", 5000
            )
        except OSError as exc:
            self.statusBar().showMessage(f"테마 설정을 저장하지 못했습니다: {exc}", 10000)

    def refresh_jobs(self) -> None:
        selected = self.current_job.job_id if self.current_job else None
        self.job_list = self.jobs.list_jobs()
        if selected:
            self.current_job = next(
                (job for job in self.job_list if job.job_id == selected), self.current_job
            )
        self.project_panel.set_jobs(self.job_list, selected)
        if self.job_list and self.current_job is None:
            self.select_job(self.job_list[0].job_id)
        elif self.current_job is not None:
            self._render_job()

    def _upsert_job(self, job: Job) -> None:
        self.job_list = [item for item in self.job_list if item.job_id != job.job_id]
        self.job_list.append(job)
        self.job_list.sort(key=lambda item: item.updated_at, reverse=True)
        index = next(
            position for position, item in enumerate(self.job_list) if item.job_id == job.job_id
        )
        selected = self.current_job.job_id if self.current_job else None
        self.project_panel.upsert_job(job, index, selected)

    def create_job(self) -> None:
        image, _ = QFileDialog.getOpenFileName(
            self, "입력 이미지 선택", "", "이미지 (*.png *.jpg *.jpeg)"
        )
        if not image:
            return
        name, ok = QInputDialog.getText(self, "새 작업", "작업 이름", text=Path(image).stem)
        if not ok:
            return
        try:
            job = self.jobs.create(name, image)
        except Exception as exc:
            QMessageBox.critical(self, "작업 생성 실패", str(exc))
            return
        self.current_job = job
        self._upsert_job(job)
        self._render_job()

    def select_job(self, job_id: str) -> None:
        if self.pipeline.busy and self.current_job and self.current_job.job_id != job_id:
            self.statusBar().showMessage(
                "실행 중에도 다른 작업을 볼 수 있지만 실행 버튼은 잠깁니다."
            )
        try:
            self.current_job = next(
                (job for job in self.job_list if job.job_id == job_id), None
            ) or self.jobs.load(job_id)
            self._render_job()
        except Exception as exc:
            QMessageBox.warning(self, "작업 열기 실패", str(exc))

    def _render_job(self) -> None:
        if not self.current_job:
            self.workspace_header.hide()
            self.workspace_stack.setCurrentWidget(self.welcome_page)
            return
        self.workspace_stack.setCurrentWidget(self.tabs)
        self.workspace_header.show()
        self.workspace_title.set_title(self.current_job.name)
        stage = self.current_job.current_stage
        title = {"modeling": "모델링", "blender": "Blender", "rigging": "리깅", "unity": "Unity"}
        set_status_badge(self.workspace_status, title[stage], self.current_job.stages[stage].status)
        self.modeling_panel.set_job(self.current_job, self.pipeline.busy)
        self.blender_panel.set_job(self.current_job, self.pipeline.busy)
        self.rigging_panel.set_job(
            self.current_job,
            self.pipeline.rigging.available_inputs(self.current_job),
            self.pipeline.busy,
        )
        self.unity_panel.set_job(self.current_job, self.pipeline.busy)
        self.unity_panel.set_recent_projects(
            [item.unity_project_path for item in self.job_list if item.unity_project_path]
        )
        if self.unity.job and self.unity.job.job_id == self.current_job.job_id:
            self.unity_panel.set_session(self.unity.session)
        else:
            self.unity_panel.set_session(None)
        self.cancel_button.setEnabled(self.pipeline.busy)
        self._update_next_steps()

    def _inspection_ready(self, payload: dict) -> None:
        job = self.pipeline._active_job
        self.blender_panel.set_inspection(
            payload,
            job_id=job.job_id if job else None,
            input_path=job.blender_input_path if job else None,
        )

    def _route_rigging_log(self, line: str) -> None:
        if self.pipeline.busy and self.pipeline._operation.startswith("rigging"):
            self.rigging_panel.append_log(line, job=self.pipeline._active_job)

    def _unity_event(self, event: dict) -> None:
        if self.current_job and self.unity.job and self.current_job.job_id == self.unity.job.job_id:
            self.unity_panel.append_event(event)

    def _unity_session_changed(self, session) -> None:
        if self.current_job and self.unity.job and self.current_job.job_id == self.unity.job.job_id:
            self.unity_panel.set_session(session)

    def _plan_ready(self, job: Job, _request) -> None:
        if self.current_job and self.current_job.job_id == job.job_id:
            self._show_panel(self.blender_panel)

    @staticmethod
    def _usable_result(value, suffix) -> bool:
        try:
            path = Path(value) if value else None
            return bool(
                path
                and path.suffix.lower() == suffix
                and path.is_file()
                and path.stat().st_size > 0
            )
        except OSError:
            return False

    def _next_step_availability(self):
        job = self.current_job
        if job is None:
            return {
                key: (False, "먼저 작업을 선택하세요.") for key in ("blender", "rigging", "unity")
            }
        if self.pipeline.busy:
            return {
                key: (False, "실행 중인 작업이 끝나면 이동할 수 있습니다.")
                for key in ("blender", "rigging", "unity")
            }
        return {
            "blender": (
                self._usable_result(job.blender_input_path, ".glb"),
                "모델 생성 후 사용할 GLB 파일이 필요합니다.",
            ),
            "rigging": (
                any(
                    self._usable_result(item.path, ".glb")
                    for item in self.pipeline.rigging.available_inputs(job)
                ),
                "이 작업에 등록된 GLB 결과가 필요합니다.",
            ),
            "unity": (
                self._usable_result(job.unity_input_path, ".fbx"),
                "리깅으로 생성된 Humanoid FBX 결과가 필요합니다.",
            ),
        }

    def _update_next_steps(self):
        states = self._next_step_availability()
        instructions = {
            "modeling": "GLB 결과를 Blender에서 편집하세요. 편집이 필요 없으면 사람형 모델을 바로 리깅할 수 있습니다.",
            "blender": "리깅 화면에서 사용할 GLB 버전과 사람형 여부를 확인한 뒤 실행하세요.",
            "rigging": "Unity 화면에서 프로젝트를 선택·연결한 뒤 ‘Unity 프로젝트로 가져오기’로 결과를 가져오세요.",
        }
        for source, card in self.next_steps.items():
            selected = {key: states[key] for key in card.buttons}
            ready = any(enabled for enabled, reason in selected.values())
            reason = next(iter(selected.values()))[1]
            card.update_state(
                instructions[source] if ready else reason,
                {
                    key: (enabled, instructions[source] if enabled else message)
                    for key, (enabled, message) in selected.items()
                },
            )

    def go_to_next_step(self, destination):
        self._update_next_steps()
        enabled, reason = self._next_step_availability()[destination]
        if not enabled:
            self.statusBar().showMessage(reason, 10000)
            return
        panels = {
            "blender": self.blender_panel,
            "rigging": self.rigging_panel,
            "unity": self.unity_panel,
        }
        self._render_job()
        self._show_panel(panels[destination])

    def choose_unity_project(self) -> None:
        self.unity_panel.choose_project()

    def connect_unity(self, project_path: str) -> None:
        if self._unity_blocked_by_pipeline():
            return
        if not self.current_job:
            QMessageBox.warning(self, "Unity 연결", "먼저 ForgeFlow Job을 선택하세요.")
            return
        try:
            session = self.unity.start_session(self.current_job, project_path)
            self.unity_panel.set_session(session)
            self._show_panel(self.unity_panel)
            self.statusBar().showMessage(
                "Unity Agent를 시작했습니다. 프로젝트 identity 확인을 기다리는 중입니다.", 10000
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Unity 연결 실패",
                f"{exc}\n\nUnity Editor에서 이 프로젝트를 연 뒤 Console의 "
                "`[McpBridge] Listening`을 확인하세요.",
            )

    def disconnect_unity(self) -> None:
        self.unity.shutdown()
        self.unity_panel.set_session(self.unity.session)

    def open_unity_project(self) -> None:
        path = self.unity_panel.project_path.text().strip()
        if path:
            self.open_path(path)

    def import_unity_fbx(self) -> None:
        if not self.current_job:
            return
        project = self.unity_panel.project_path.text().strip()
        try:
            result = self.unity.import_humanoid_fbx(self.current_job, project)
            QMessageBox.information(
                self,
                "FBX 가져오기 완료",
                f"Asset: {result.asset_path}\n원본 SHA-256: {result.source_sha256}\n\n"
                "기존 Asset은 덮어쓰지 않았으며 .meta는 Unity가 생성합니다.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "FBX 가져오기 실패", str(exc))

    def send_unity_prompt(
        self, text: str, include_asset: bool, include_scene: bool, analyze_screenshot: bool
    ) -> None:
        if self._unity_blocked_by_pipeline():
            return
        if not self.current_job:
            return
        if not self.unity.job or self.unity.job.job_id != self.current_job.job_id:
            QMessageBox.warning(self, "Unity 명령", "현재 Job으로 Unity 세션을 다시 연결하세요.")
            return
        try:
            self.unity.send_prompt(
                text,
                include_asset=include_asset,
                include_current_scene=include_scene,
                analyze_screenshot=analyze_screenshot,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Unity 명령 전송 실패", str(exc))

    def cancel_unity(self) -> None:
        self.unity.cancel_active()
        self.statusBar().showMessage(
            "Unity Agent 실행을 취소했습니다. 부분 산출물과 로그는 보존됩니다. "
            "다음 연결에서 Play Mode와 입력 상태를 확인하세요.",
            15000,
        )

    def accept_unity_review(self, turn_id: str, note: str) -> None:
        try:
            self.unity.review_turn(turn_id, True, note)
        except Exception as exc:
            QMessageBox.critical(self, "검토 저장 실패", str(exc))

    def reject_unity_review(self, turn_id: str, note: str) -> None:
        try:
            self.unity.review_turn(turn_id, False, note)
        except Exception as exc:
            QMessageBox.critical(self, "검토 저장 실패", str(exc))

    def repair_unity_turn(
        self,
        turn_id: str,
        feedback: str,
        include_asset: bool,
        include_scene: bool,
        analyze_screenshot: bool,
    ) -> None:
        if self._unity_blocked_by_pipeline():
            return
        try:
            self.unity.send_review_repair(
                turn_id,
                feedback,
                include_asset=include_asset,
                include_current_scene=include_scene,
                analyze_screenshot=analyze_screenshot,
            )
        except Exception as exc:
            QMessageBox.critical(self, "수정 요청 전송 실패", str(exc))

    def _unity_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 20000)
        self.log_panel.append(f"[Unity 오류] {message}")

    def focus_unity(self) -> None:
        if os.name != "nt":
            return
        found: list[int] = []
        user32 = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)

        def visit(hwnd, _lparam):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                if "Unity" in title.value and user32.IsWindowVisible(hwnd):
                    found.append(hwnd)
                    return False
            return True

        user32.EnumWindows(callback_type(visit), 0)
        if found:
            user32.ShowWindow(found[0], 9)
            user32.SetForegroundWindow(found[0])
        else:
            self.statusBar().showMessage("열린 Unity Editor 창을 찾지 못했습니다.", 8000)

    def start_modeling(self) -> None:
        if not self.current_job or self.pipeline.busy:
            return
        if self._gpu_blocked_by_unity():
            return
        try:
            self.pipeline.start_modeling(self.current_job)
            self._render_job()
        except Exception as exc:
            QMessageBox.critical(self, "모델 생성 시작 실패", str(exc))

    def inspect_scene(self) -> None:
        if not self.current_job or self.pipeline.busy:
            return
        try:
            self.pipeline.start_inspect(
                self.current_job,
                suffix=f"v{self.current_job.latest_blender_request.version:03d}"
                if self.current_job.latest_blender_request
                else "source",
            )
            self._render_job()
        except Exception as exc:
            QMessageBox.critical(self, "장면 검사 실패", str(exc))

    def propose_blender(self, request: str) -> None:
        if not self.current_job or self.pipeline.busy:
            return
        try:
            self.pipeline.start_proposal(self.current_job, request)
            self._render_job()
        except Exception as exc:
            QMessageBox.critical(self, "계획 생성 실패", str(exc))

    def approve_blender(self) -> None:
        if not self.current_job or self.pipeline.busy:
            return
        try:
            self.pipeline.approve(self.current_job)
            self._render_job()
        except Exception as exc:
            QMessageBox.critical(self, "Blender 실행 실패", str(exc))

    def deny_blender(self) -> None:
        if self.current_job:
            self.pipeline.deny(self.current_job)

    def start_rigging(self, selected_input: str, seed: int) -> None:
        if not self.current_job or self.pipeline.busy:
            return
        if self._gpu_blocked_by_unity():
            return
        version = self.jobs.next_rigging_version(self.current_job)
        output = self.jobs.job_directory(self.current_job.job_id) / "rigging" / f"v{version:03d}"
        answer = QMessageBox.question(
            self,
            "Humanoid 자동 리깅 최종 확인",
            "사람형 이족보행 입력임을 확인한 뒤 UniRig을 실행합니다.\n\n"
            f"입력: {Path(selected_input).resolve(strict=False)}\n"
            f"출력: {output.resolve(strict=False)}\n"
            f"Seed: {seed}\n\n"
            "원본 GLB는 변경하지 않으며 PASS는 구조 검증만 의미합니다.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self._gpu_blocked_by_unity():
            return
        try:
            self.pipeline.start_rigging(self.current_job, selected_input, seed)
            self._show_panel(self.rigging_panel)
            self._render_job()
        except Exception as exc:
            QMessageBox.critical(self, "자동 리깅 시작 실패", str(exc))

    def _unity_blocked_by_pipeline(self) -> bool:
        if self.pipeline.busy:
            QMessageBox.warning(
                self,
                "Unity 작업 대기",
                "모델링·Blender·리깅 작업이 끝난 뒤 Unity 명령을 실행하세요.",
            )
            return True
        return False

    def _gpu_blocked_by_unity(self) -> bool:
        if self.unity.busy:
            QMessageBox.warning(
                self,
                "GPU 작업 대기",
                "Unity 연결 또는 명령 처리 중입니다. 완료되거나 취소한 뒤 실행하세요.",
            )
            return True
        return False

    def _job_changed(self, job: Job) -> None:
        is_current = bool(self.current_job and self.current_job.job_id == job.job_id)
        if is_current:
            self.current_job = job
        self._upsert_job(job)
        if is_current:
            self._render_job()

    def _operation_finished(self, operation: str, success: bool, message: str) -> None:
        self.statusBar().showMessage(message, 15000)
        self.log_panel.append(("[완료] " if success else "[실패] ") + message)
        self._render_job()
        active = self.pipeline._active_job
        current = active is None or bool(
            self.current_job and self.current_job.job_id == active.job_id
        )
        if current and success and operation in {"modeling", "blender_plan", "blender", "rigging"}:
            if operation == "modeling":
                self._show_panel(self.modeling_panel)
            elif operation == "rigging":
                self._show_panel(self.rigging_panel)
            else:
                self._show_panel(self.blender_panel)
        if current and not success and operation not in {"blender_plan"}:
            QMessageBox.warning(self, "실행 결과", message)

    def open_path(self, value: str) -> None:
        path = Path(value)
        if path.exists():
            os.startfile(str(path))

    def open_job_folder(self) -> None:
        if self.current_job:
            os.startfile(str(self.jobs.job_directory(self.current_job.job_id)))

    def _toggle_environment_details(self, expanded: bool) -> None:
        self.environment_details.setVisible(expanded)
        self.environment_toggle.setText("상세 접기" if expanded else "상세 보기")

    def refresh_environment(self) -> None:
        if self.environment_worker and self.environment_worker.isRunning():
            return
        self.refresh_environment_button.setEnabled(False)
        self.environment_summary.setText("환경 확인 중…")
        self.environment_summary.setToolTip("연결 상태와 설치 모델을 확인하고 있습니다.")
        self.environment_summary.setProperty("envState", "checking")
        self._refresh_widget_style(self.environment_summary)
        for label in self.environment_labels.values():
            heading, separator, model = label.text().partition("\n")
            label.setText(
                heading.split(":")[0] + ": 확인 중" + (separator + model if separator else "")
            )
            label.setProperty("envState", "checking")
            self._refresh_widget_style(label)
        self.environment_worker = EnvironmentWorker(self.config, self)
        self.environment_worker.completed.connect(self._environment_ready)
        self.environment_worker.finished.connect(
            lambda: self.refresh_environment_button.setEnabled(True)
        )
        self.environment_worker.start()

    def _environment_ready(self, checks: dict) -> None:
        names = {
            "modeling": "Pixal3D",
            "gpu": "GPU",
            "wsl": "WSL",
            "ollama_blender": "Blender 모델",
            "ollama_unity": "Unity 모델",
            "mcp": "Blender MCP",
            "unirig": "UniRig",
            "blender": "Blender",
            "unity_mcp": "Unity MCP",
        }
        issues = []
        for key, label in self.environment_labels.items():
            check = checks.get(key, {"ok": False, "detail": "점검 결과 없음"})
            label.setText(
                f"{'●' if check['ok'] else '○'} {names[key]}: {'연결됨' if check['ok'] else '실패'}"
            )
            if key in {"ollama_blender", "ollama_unity"}:
                model = (
                    self.config.ollama_model
                    if key == "ollama_blender"
                    else self.config.unity_agent_model
                )
                label.setText(
                    f"{'●' if check['ok'] else '○'} {names[key]}: {check.get('status', '점검 실패')}\n{model}"
                )
            label.setToolTip(str(check["detail"]))
            needs_attention = not check["ok"] or bool(check.get("warning"))
            if needs_attention:
                issues.append(f"{names[key]}: {check['detail']}")
            if check.get("warning") and check["ok"]:
                label.setText(label.text().replace("연결됨", "확인 필요"))
            label.setProperty("envState", "error" if needs_attention else "ok")
            self._refresh_widget_style(label)
        self.environment_summary.setText(f"확인 필요 {len(issues)}건" if issues else "환경 정상")
        self.environment_summary.setToolTip(
            "\n\n".join(issues) if issues else "연결 상태와 설치 모델 확인을 통과했습니다."
        )
        self.environment_summary.setProperty("envState", "error" if issues else "ok")
        self._refresh_widget_style(self.environment_summary)

    @staticmethod
    def _refresh_widget_style(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def edit_settings(self) -> None:
        dialog = SettingsDialog(self.saved_config, self)
        if dialog.exec():
            updated = dialog.value(self.saved_config)
            try:
                updated.save()
            except OSError as exc:
                QMessageBox.warning(self, "설정 저장 실패", str(exc))
                return
            self.saved_config = updated
            QMessageBox.information(
                self,
                "설정 저장",
                "설정을 저장했습니다. 안전한 적용을 위해 ForgeFlow를 다시 실행해 주세요.",
            )

    def closeEvent(self, event) -> None:
        if self.pipeline.busy or self.unity.running:
            answer = QMessageBox.question(
                self, "실행 중 종료", "외부 프로세스가 실행 중입니다. 취소하고 종료할까요?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.pipeline.cancel_active()
        if self.unity.running:
            self.unity.shutdown()
        if self.environment_worker and self.environment_worker.isRunning():
            self.environment_worker.requestInterruption()
            if not self.environment_worker.wait(2000):
                # Environment checks are read-only and may be blocked inside a
                # third-party command. Do not leave a live QThread behind while
                # Qt tears down the window.
                self.environment_worker.terminate()
                self.environment_worker.wait(2000)
        event.accept()
