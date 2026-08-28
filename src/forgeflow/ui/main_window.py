from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
    QMainWindow, QMessageBox, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from forgeflow.adapters.blender_adapter import BlenderAdapter
from forgeflow.adapters.modeling_adapter import ModelingAdapter
from forgeflow.adapters.rigging_adapter import RiggingAdapter
from forgeflow.config import AppConfig
from forgeflow.domain.job import Job
from forgeflow.services.environment_service import EnvironmentWorker
from forgeflow.services.job_service import JobService
from forgeflow.services.pipeline_service import PipelineService

from .blender_panel import BlenderPanel
from .log_panel import LogPanel
from .modeling_panel import ModelingPanel
from .project_panel import ProjectPanel
from .rigging_panel import RiggingPanel
from .settings_dialog import SettingsDialog
from .theme import build_stylesheet, normalize_theme


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig | None = None):
        super().__init__()
        self.config = config or AppConfig.load()
        self.jobs = JobService(self.config.jobs_root)
        self.job_list = self.jobs.recover_interrupted()
        self.current_job: Job | None = None
        self.pipeline = PipelineService(
            self.jobs, ModelingAdapter(self.config, self.jobs), BlenderAdapter(self.config, self.jobs),
            RiggingAdapter(self.config, self.jobs), self
        )
        self.environment_worker: EnvironmentWorker | None = None
        self._build_ui()
        self._connect()
        self.refresh_jobs()
        self.refresh_environment()

    def _build_ui(self) -> None:
        self.setWindowTitle("ForgeFlow — 이미지→3D·Blender 통합 워크플로")
        self.resize(1480, 940)
        central = QWidget()
        root = QVBoxLayout(central)
        environment_box = QGroupBox("환경 연결 상태")
        env_layout = QHBoxLayout(environment_box)
        self.environment_labels = {}
        for key, label in (
            ("modeling", "Pixal3D"), ("gpu", "GPU"), ("wsl", "WSL"),
            ("ollama", "Ollama"), ("mcp", "Blender MCP"), ("unirig", "UniRig"),
            ("blender", "Blender"),
        ):
            widget = QLabel(f"● {label}: 확인 중")
            widget.setProperty("envState", "checking")
            self.environment_labels[key] = widget
            env_layout.addWidget(widget)
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
        self.cancel_button = QPushButton("실행 취소")
        self.cancel_button.setEnabled(False)
        env_layout.addWidget(self.refresh_environment_button)
        env_layout.addWidget(self.cancel_button)
        root.addWidget(environment_box)
        splitter = QSplitter()
        self.project_panel = ProjectPanel()
        self.project_panel.setMinimumWidth(280)
        splitter.addWidget(self.project_panel)
        self.tabs = QTabWidget()
        self.modeling_panel = ModelingPanel()
        self.blender_panel = BlenderPanel()
        self.rigging_panel = RiggingPanel()
        self.log_panel = LogPanel()
        self.tabs.addTab(self.modeling_panel, "1. 이미지 → 3D")
        self.tabs.addTab(self.blender_panel, "2. Blender 자연어 편집")
        self.tabs.addTab(self.rigging_panel, "3. Humanoid 리깅")
        self.tabs.addTab(self.log_panel, "실행 로그")
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("준비됨")
        self._apply_theme(selected_theme)

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
        self.pipeline.log_received.connect(self.log_panel.append)
        self.pipeline.log_received.connect(self.rigging_panel.append_log)
        self.pipeline.job_changed.connect(self._job_changed)
        self.pipeline.inspect_ready.connect(self.blender_panel.set_inspection)
        self.pipeline.plan_ready.connect(lambda _job, _request: self.tabs.setCurrentWidget(self.blender_panel))
        self.pipeline.operation_finished.connect(self._operation_finished)
        self.refresh_environment_button.clicked.connect(self.refresh_environment)
        self.cancel_button.clicked.connect(self.pipeline.cancel_active)
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
            self.config.save()
            self.statusBar().showMessage(
                f"{'라이트' if name == 'light' else '다크'} 테마를 적용하고 저장했습니다.", 5000
            )
        except OSError as exc:
            self.statusBar().showMessage(f"테마 설정을 저장하지 못했습니다: {exc}", 10000)

    def refresh_jobs(self) -> None:
        selected = self.current_job.job_id if self.current_job else None
        self.job_list = self.jobs.list_jobs()
        self.project_panel.set_jobs(self.job_list, selected)
        if self.job_list and self.current_job is None:
            self.select_job(self.job_list[0].job_id)

    def create_job(self) -> None:
        image, _ = QFileDialog.getOpenFileName(self, "입력 이미지 선택", "", "이미지 (*.png *.jpg *.jpeg)")
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
        self.refresh_jobs()
        self.select_job(job.job_id)

    def select_job(self, job_id: str) -> None:
        if self.pipeline.busy and self.current_job and self.current_job.job_id != job_id:
            self.statusBar().showMessage("실행 중에도 다른 작업을 볼 수 있지만 실행 버튼은 잠깁니다.")
        try:
            self.current_job = self.jobs.load(job_id)
            self._render_job()
        except Exception as exc:
            QMessageBox.warning(self, "작업 열기 실패", str(exc))

    def _render_job(self) -> None:
        if not self.current_job:
            return
        self.modeling_panel.set_job(self.current_job, self.pipeline.busy)
        self.blender_panel.set_job(self.current_job, self.pipeline.busy)
        self.rigging_panel.set_job(
            self.current_job, self.pipeline.rigging.available_inputs(self.current_job), self.pipeline.busy
        )
        self.cancel_button.setEnabled(self.pipeline.busy)

    def start_modeling(self) -> None:
        if not self.current_job or self.pipeline.busy:
            return
        try:
            self.pipeline.start_modeling(self.current_job)
            self.tabs.setCurrentWidget(self.log_panel)
            self._render_job()
        except Exception as exc:
            QMessageBox.critical(self, "모델 생성 시작 실패", str(exc))

    def inspect_scene(self) -> None:
        if not self.current_job or self.pipeline.busy:
            return
        try:
            self.pipeline.start_inspect(self.current_job, suffix=f"v{self.current_job.latest_blender_request.version:03d}" if self.current_job.latest_blender_request else "source")
            self.tabs.setCurrentWidget(self.log_panel)
            self._render_job()
        except Exception as exc:
            QMessageBox.critical(self, "장면 검사 실패", str(exc))

    def propose_blender(self, request: str) -> None:
        if not self.current_job or self.pipeline.busy:
            return
        try:
            self.pipeline.start_proposal(self.current_job, request)
            self.tabs.setCurrentWidget(self.log_panel)
            self._render_job()
        except Exception as exc:
            QMessageBox.critical(self, "계획 생성 실패", str(exc))

    def approve_blender(self) -> None:
        if not self.current_job or self.pipeline.busy:
            return
        try:
            self.pipeline.approve(self.current_job)
            self.tabs.setCurrentWidget(self.log_panel)
            self._render_job()
        except Exception as exc:
            QMessageBox.critical(self, "Blender 실행 실패", str(exc))

    def deny_blender(self) -> None:
        if self.current_job:
            self.pipeline.deny(self.current_job)

    def start_rigging(self, selected_input: str, seed: int) -> None:
        if not self.current_job or self.pipeline.busy:
            return
        version = self.jobs.next_rigging_version(self.current_job)
        output = self.jobs.job_directory(self.current_job.job_id) / "rigging" / f"v{version:03d}"
        answer = QMessageBox.question(
            self, "Humanoid 자동 리깅 최종 확인",
            "사람형 이족보행 입력임을 확인한 뒤 UniRig을 실행합니다.\n\n"
            f"입력: {Path(selected_input).resolve(strict=False)}\n"
            f"출력: {output.resolve(strict=False)}\n"
            f"Seed: {seed}\n\n"
            "원본 GLB는 변경하지 않으며 PASS는 구조 검증만 의미합니다.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.rigging_panel.clear_log()
            self.pipeline.start_rigging(self.current_job, selected_input, seed)
            self.tabs.setCurrentWidget(self.rigging_panel)
            self._render_job()
        except Exception as exc:
            QMessageBox.critical(self, "자동 리깅 시작 실패", str(exc))

    def _job_changed(self, job: Job) -> None:
        if self.current_job and self.current_job.job_id == job.job_id:
            self.current_job = job
        self.refresh_jobs()
        self._render_job()

    def _operation_finished(self, operation: str, success: bool, message: str) -> None:
        self.statusBar().showMessage(message, 15000)
        self.log_panel.append(("[완료] " if success else "[실패] ") + message)
        if self.current_job:
            self.current_job = self.jobs.load(self.current_job.job_id)
        self.refresh_jobs()
        self._render_job()
        if success and operation in {"modeling", "blender_plan", "blender", "rigging"}:
            if operation == "modeling":
                self.tabs.setCurrentWidget(self.modeling_panel)
            elif operation == "rigging":
                self.tabs.setCurrentWidget(self.rigging_panel)
            else:
                self.tabs.setCurrentWidget(self.blender_panel)
        if not success and operation not in {"blender_plan"}:
            QMessageBox.warning(self, "실행 결과", message)

    def open_path(self, value: str) -> None:
        path = Path(value)
        if path.exists():
            os.startfile(str(path))

    def open_job_folder(self) -> None:
        if self.current_job:
            os.startfile(str(self.jobs.job_directory(self.current_job.job_id)))

    def refresh_environment(self) -> None:
        if self.environment_worker and self.environment_worker.isRunning():
            return
        self.refresh_environment_button.setEnabled(False)
        for label in self.environment_labels.values():
            label.setText(label.text().split(":")[0] + ": 확인 중")
            label.setProperty("envState", "checking")
            self._refresh_widget_style(label)
        self.environment_worker = EnvironmentWorker(self.config, self)
        self.environment_worker.completed.connect(self._environment_ready)
        self.environment_worker.finished.connect(lambda: self.refresh_environment_button.setEnabled(True))
        self.environment_worker.start()

    def _environment_ready(self, checks: dict) -> None:
        names = {"modeling": "Pixal3D", "gpu": "GPU", "wsl": "WSL", "ollama": "Ollama",
                 "mcp": "Blender MCP", "unirig": "UniRig", "blender": "Blender"}
        for key, label in self.environment_labels.items():
            check = checks.get(key, {"ok": False, "detail": "점검 결과 없음"})
            label.setText(f"{'●' if check['ok'] else '○'} {names[key]}: {'연결됨' if check['ok'] else '실패'}")
            label.setToolTip(str(check["detail"]))
            label.setProperty("envState", "ok" if check["ok"] else "error")
            self._refresh_widget_style(label)

    @staticmethod
    def _refresh_widget_style(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def edit_settings(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if dialog.exec():
            updated = dialog.value(self.config)
            updated.save()
            QMessageBox.information(self, "설정 저장", "설정을 저장했습니다. 안전한 적용을 위해 ForgeFlow를 다시 실행해 주세요.")

    def closeEvent(self, event) -> None:
        if self.pipeline.busy:
            answer = QMessageBox.question(self, "실행 중 종료", "외부 프로세스가 실행 중입니다. 취소하고 종료할까요?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.pipeline.cancel_active()
        if self.environment_worker and self.environment_worker.isRunning():
            self.environment_worker.requestInterruption()
            self.environment_worker.wait(2000)
        event.accept()
