from __future__ import annotations

import json
import os
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..status import status_text


class RiggingPanel(QWidget):
    run_requested = Signal(str, int)
    open_file_requested = Signal(str)
    open_folder_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._job_id: str | None = None
        self._context: tuple | None = None
        self._logs: dict[tuple, deque[str]] = {}
        self._phases: dict[tuple, str] = {}
        self._selected_inputs: dict[str, str] = {}
        self._seeds: dict[str, int] = {}
        self._busy = False
        layout = QVBoxLayout(self)
        guidance = QLabel(
            "정면 대칭 T-pose 또는 A-pose의 이족보행 Humanoid 권장 · 얼굴 리그 미포함 · "
            "갑옷/치마/장식은 웨이트 수동 보정이 필요할 수 있음 · 자동 리토폴로지 미수행 · "
            "고폴리 모델은 게임 투입 전 최적화 필요\n"
            "PASS는 본/웨이트/파일 구조 검증 통과이며 애니메이션·육안 품질 보장이 아닙니다."
        )
        guidance.setWordWrap(True)
        guidance.setObjectName("guidance")
        layout.addWidget(guidance)

        input_box = QGroupBox("리깅 입력 — 이 작업에 등록된 GLB만 선택 가능")
        form = QFormLayout(input_box)
        self.inputs = QComboBox()
        self.inputs.currentIndexChanged.connect(self._input_changed)
        self.input_path = QLabel("없음")
        self.input_path.setWordWrap(True)
        self.seed = QSpinBox()
        self.seed.setRange(0, 2_147_483_647)
        self.seed.setValue(12345)
        self.confirm_humanoid = QCheckBox("이 입력이 사람형 이족보행 모델임을 확인했습니다.")
        self.confirm_humanoid.toggled.connect(self._toggle_ready)
        form.addRow("GLB 버전", self.inputs)
        form.addRow("절대 입력 경로", self.input_path)
        form.addRow("Seed", self.seed)
        form.addRow("실행 확인", self.confirm_humanoid)
        layout.addWidget(input_box)

        row = QHBoxLayout()
        self.run_button = QPushButton("자동 리깅 실행")
        self.run_button.clicked.connect(self._emit_run)
        self.open_fbx = QPushButton("Humanoid FBX 열기")
        self.open_blend = QPushButton("Humanoid BLEND 열기")
        self.open_folder = QPushButton("결과 폴더 열기")
        row.addWidget(self.run_button)
        row.addWidget(self.open_fbx)
        row.addWidget(self.open_blend)
        row.addWidget(self.open_folder)
        layout.addLayout(row)

        self.status = QLabel("리깅: pending")
        self.phase_status = QLabel("세부 단계: 대기")
        self.unity_path = QLabel("다음 Unity 입력: 없음")
        self.unity_path.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(self.phase_status)
        layout.addWidget(self.unity_path)
        self.quality_summary = QLabel("품질 보고: 리깅 결과 생성 후 확인할 수 있습니다.")
        self.quality_summary.setWordWrap(True)
        self.quality_summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.quality_summary)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        summary_box = QGroupBox("리그 구조 요약")
        summary_layout = QFormLayout(summary_box)
        self.summary_values: dict[str, QLabel] = {}
        for key, label in (
            ("bone_count", "골격 수"),
            ("vertex_count", "스킨 정점 수"),
            ("weighted_vertices", "웨이트 정점 수"),
            ("max_influences", "최대 영향 본 수"),
            ("missing_required_bones", "누락 필수 본"),
        ):
            widget = QLabel("-")
            widget.setWordWrap(True)
            self.summary_values[key] = widget
            summary_layout.addRow(label, widget)
        splitter.addWidget(summary_box)

        previews = QWidget()
        preview_layout = QHBoxLayout(previews)
        self.rest_preview = self._preview_label("Rest 미리보기")
        self.pose_preview = self._preview_label("Pose 미리보기")
        preview_layout.addWidget(self.rest_preview)
        preview_layout.addWidget(self.pose_preview)
        splitter.addWidget(previews)
        layout.addWidget(splitter, 1)

        layout.addWidget(QLabel("리깅 실시간 로그"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setMaximumHeight(170)
        layout.addWidget(self.log)
        self._fbx_path: str | None = None
        self._blend_path: str | None = None
        self._folder_path: str | None = None
        self._confirmed_input: str | None = None
        self.open_fbx.clicked.connect(
            lambda: self._fbx_path and self.open_file_requested.emit(self._fbx_path)
        )
        self.open_blend.clicked.connect(
            lambda: self._blend_path and self.open_file_requested.emit(self._blend_path)
        )
        self.open_folder.clicked.connect(
            lambda: self._folder_path and self.open_folder_requested.emit(self._folder_path)
        )

    @staticmethod
    def _preview_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumSize(260, 220)
        label.setObjectName("previewFrame")
        return label

    def _input_changed(self) -> None:
        current = str(self.inputs.currentData() or "")
        self.input_path.setText(current or "없음")
        if self.confirm_humanoid.isChecked() and current != self._confirmed_input:
            self.confirm_humanoid.setChecked(False)

    def _emit_run(self) -> None:
        value = self.inputs.currentData()
        if value:
            self.run_requested.emit(str(value), self.seed.value())

    def clear_log(self) -> None:
        self.log.clear()
        self.phase_status.setText("세부 단계: 환경/VRAM 준비")
        if self._context is not None:
            self._logs.pop(self._context, None)
            self._phases.pop(self._context, None)

    @staticmethod
    def _run_key(job) -> tuple:
        request = job.latest_rigging_request
        input_path = request.input_path if request else (job.rigging_input_path or "")
        return (
            job.job_id,
            os.path.normcase(os.path.normpath(input_path)) if input_path else "",
            request.version if request else None,
            request.created_at if request else None,
        )

    def append_log(self, line: str, *, job=None) -> None:
        context = self._run_key(job) if job is not None else self._context
        if context is None:
            return
        self._logs.setdefault(context, deque(maxlen=5000)).append(line)
        phase = self._phases.get(context, "세부 단계: 환경/VRAM 준비")
        lowered = line.lower()
        if "_skeleton.fbx" in lowered or "skeleton/articulation" in lowered:
            phase = "세부 단계: 1/5 UniRig 골격 생성"
        if "_skin.fbx" in lowered or "/skin/articulation" in lowered:
            phase = "세부 단계: 2/5 UniRig 스키닝"
        if "_rigged.glb" in lowered:
            phase = "세부 단계: 3/5 원본 메시와 리그 병합"
        if "_humanoid.fbx" in lowered or "humanoid auto-rig completed" in lowered:
            phase = "세부 단계: 4/5 Blender Humanoid 후처리/구조 검증"
        if "preview saved" in lowered:
            phase = "세부 단계: 5/5 rest/pose 미리보기"
        self._phases[context] = phase
        if context == self._context:
            self.log.appendPlainText(line)
            self.phase_status.setText(phase)

    def set_job(self, job, inputs, busy: bool = False) -> None:
        self._busy = busy
        changed_job = job.job_id != self._job_id
        if self._job_id is not None:
            self._selected_inputs[self._job_id] = str(self.inputs.currentData() or "")
            self._seeds[self._job_id] = self.seed.value()
        previous = self._selected_inputs.get(job.job_id, "")
        if changed_job:
            self._job_id = job.job_id
            self.confirm_humanoid.setChecked(False)
            self._confirmed_input = None
            self.seed.setValue(self._seeds.get(job.job_id, 12345))
        context = self._run_key(job)
        if context != self._context:
            self._context = context
            self.log.setPlainText("\n".join(self._logs.get(context, ())))
        self.inputs.blockSignals(True)
        self.inputs.clear()
        for candidate in inputs:
            self.inputs.addItem(candidate.label, str(candidate.path))
        if previous:
            index = self.inputs.findData(previous)
            if index >= 0:
                self.inputs.setCurrentIndex(index)
        self.inputs.blockSignals(False)
        self._input_changed()

        state = job.stages["rigging"]
        request = job.latest_rigging_request
        detail = (
            f" · {state.error or state.stale_reason}" if (state.error or state.stale_reason) else ""
        )
        if request and request.preview_warning:
            detail += f" · 미리보기 경고: {request.preview_warning}"
        self.status.setText(f"리깅: {status_text(state.status)}{detail}")
        if state.status == "completed":
            self.phase_status.setText("세부 단계: 완료")
        elif state.status in {"failed", "cancelled"}:
            self.phase_status.setText(f"세부 단계: {status_text(state.status)}")
        elif state.status == "running":
            self.phase_status.setText(self._phases.get(context, "세부 단계: 환경/VRAM 준비"))
        else:
            self.phase_status.setText("세부 단계: 대기")
        self.unity_path.setText("다음 Unity 입력: " + (job.unity_input_path or "없음"))
        self._fbx_path = job.humanoid_fbx_path
        self._blend_path = job.humanoid_blend_path
        self._folder_path = request.output_directory if request else None
        self.open_fbx.setEnabled(bool(self._fbx_path and Path(self._fbx_path).is_file()))
        self.open_blend.setEnabled(bool(self._blend_path and Path(self._blend_path).is_file()))
        self.open_folder.setEnabled(bool(self._folder_path and Path(self._folder_path).is_dir()))

        report = None
        if request and request.report_path and Path(request.report_path).is_file():
            try:
                report = json.loads(Path(request.report_path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                report = None
        quality = (report or {}).get("quality", {})
        warnings = (report or {}).get("warnings", [])
        if quality:
            triangles = quality.get("triangle_count")
            triangle_text = f"{triangles:,}" if isinstance(triangles, int) else "-"
            self.quality_summary.setText(
                f"품질 보고 · 메시 {quality.get('mesh_count', '-')}개 · 삼각형 {triangle_text}개"
                f" · 재질 {quality.get('material_count', '-')}개\n"
                + (
                    "검토 필요: " + " / ".join(self._quality_warning(item) for item in warnings[:4])
                    if warnings
                    else "구조 검사 완료 · 변형과 애니메이션 품질은 별도 검토하세요."
                )
            )
        else:
            self.quality_summary.setText(
                "기존 구조 보고서 · 모든 메시의 weight와 게임 성능 예산은 별도 확인이 필요합니다."
                if report
                else "품질 보고: 리깅 결과 생성 후 확인할 수 있습니다."
            )
        for key, widget in self.summary_values.items():
            value = report.get(key) if report else None
            if isinstance(value, list):
                value = ", ".join(value) if value else "없음"
            widget.setText("-" if value is None else str(value))

        artifacts = {
            item.kind: item.path
            for item in job.artifacts
            if item.stage == "rigging" and (request is None or item.version == request.version)
        }
        self._set_preview(self.rest_preview, artifacts.get("rest_preview"), "Rest 미리보기")
        self._set_preview(self.pose_preview, artifacts.get("pose_preview"), "Pose 미리보기")
        ready = bool(inputs) and self.confirm_humanoid.isChecked()
        self.run_button.setEnabled(ready and not busy)
        self.inputs.setEnabled(not busy)
        self.seed.setEnabled(not busy)
        self.confirm_humanoid.setEnabled(not busy)

    def _toggle_ready(self, checked: bool) -> None:
        if checked:
            self._confirmed_input = str(self.inputs.currentData() or "")
        self.run_button.setEnabled(checked and self.inputs.count() > 0 and not self._busy)

    @staticmethod
    def _quality_warning(value) -> str:
        text = str(value)
        if ":unbound_static_preserved:" in text:
            name = text.partition(":")[0]
            return f"{name}: 스킨이 없는 부속 메시를 보존했습니다. 캐릭터와 함께 움직이는지 확인하세요."
        return text

    @staticmethod
    def _set_preview(label: QLabel, path: str | None, fallback: str) -> None:
        pixmap = QPixmap(path) if path else QPixmap()
        if pixmap.isNull():
            label.setPixmap(QPixmap())
            label.setText(fallback)
        else:
            label.setText("")
            label.setPixmap(
                pixmap.scaled(
                    360,
                    300,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
