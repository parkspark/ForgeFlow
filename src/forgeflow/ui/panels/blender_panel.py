from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..status import status_text


class BlenderPanel(QWidget):
    inspect_requested = Signal()
    propose_requested = Signal(str)
    approve_requested = Signal()
    deny_requested = Signal()
    open_file_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._context: tuple[str, str] | None = None
        self._drafts: dict[tuple[str, str], str] = {}
        self._inspections: dict[tuple[str, str], str] = {}
        layout = QVBoxLayout(self)
        supported = QLabel(
            "지원: 장면/재질 검사, 재질 속성, 위치·회전·크기, Bevel, Decimate, Smooth shading, GLB/BLEND/FBX 내보내기\n"
            "미지원: 자유 메시 모델링, 임의 Blender Python, UV 편집, 리토폴로지, 리깅"
        )
        supported.setWordWrap(True)
        layout.addWidget(supported)
        self.input_label = QLabel("Blender 입력: 없음")
        self.input_label.setWordWrap(True)
        layout.addWidget(self.input_label)
        inspect_row = QHBoxLayout()
        self.inspect = QPushButton("장면 검사")
        self.inspect.clicked.connect(self.inspect_requested)
        inspect_row.addWidget(self.inspect)
        inspect_row.addStretch()
        layout.addLayout(inspect_row)
        self.scene = QPlainTextEdit()
        self.scene.setReadOnly(True)
        self.scene.setPlaceholderText("검사 후 실제 오브젝트와 재질 이름이 여기에 표시됩니다.")
        self.scene.setMaximumHeight(150)
        layout.addWidget(self.scene)
        layout.addWidget(QLabel("한국어 편집 요청"))
        self.request = QPlainTextEdit()
        self.request.setPlaceholderText(
            "예: geometry_0을 1.05배 확대하고 smooth shading을 적용해줘"
        )
        self.request.setMaximumHeight(100)
        layout.addWidget(self.request)
        self.propose = QPushButton("실행 계획 만들기")
        self.propose.clicked.connect(
            lambda: self.propose_requested.emit(self.request.toPlainText())
        )
        layout.addWidget(self.propose)
        plan_box = QGroupBox("제안된 실행 계획 — 승인 전에는 쓰기 작업이 실행되지 않습니다")
        plan_layout = QVBoxLayout(plan_box)
        self.plan = QPlainTextEdit()
        self.plan.setReadOnly(True)
        plan_layout.addWidget(self.plan)
        approval = QHBoxLayout()
        self.approve = QPushButton("승인하고 실행")
        self.approve.clicked.connect(self.approve_requested)
        self.deny = QPushButton("취소")
        self.deny.clicked.connect(self.deny_requested)
        approval.addWidget(self.approve)
        approval.addWidget(self.deny)
        plan_layout.addLayout(approval)
        layout.addWidget(plan_box)
        layout.addWidget(QLabel("Blender 결과 버전"))
        self.versions = QListWidget()
        self.versions.itemDoubleClicked.connect(
            lambda item: self.open_file_requested.emit(item.data(256))
        )
        layout.addWidget(self.versions)

    @staticmethod
    def _key(job_id: str, input_path: str | None) -> tuple[str, str]:
        return str(job_id), os.path.normcase(os.path.normpath(input_path)) if input_path else ""

    def set_inspection(
        self, payload: dict, *, job_id: str | None = None, input_path: str | None = None,
    ) -> None:
        context = self._key(job_id, input_path) if job_id is not None else self._context
        if context is None:
            return
        data = payload.get("data", {})
        lines = ["오브젝트:"]
        for item in data.get("objects", []):
            lines.append(
                f"- {item.get('name')} ({item.get('type')}), dimensions={item.get('dimensions')}, materials={item.get('material_slots')}"
            )
        materials = data.get("materials", [])
        if materials:
            lines.append("재질: " + ", ".join(item.get("name", "") for item in materials))
        text = "\n".join(lines)
        self._inspections[context] = text
        if context == self._context:
            self.scene.setPlainText(text)

    def set_job(self, job, busy: bool = False) -> None:
        context = self._key(job.job_id, job.blender_input_path)
        if context != self._context:
            if self._context is not None:
                self._drafts[self._context] = self.request.toPlainText()
            self._context = context
            self.request.setPlainText(self._drafts.get(context, ""))
            self.scene.setPlainText(self._inspections.get(context, ""))
        self.input_label.setText("Blender 입력: " + (job.blender_input_path or "없음"))
        request = job.latest_blender_request
        awaiting = bool(request and request.status == "awaiting_approval")
        matching_input = bool(
            request and self._key(job.job_id, request.input_path) == context
        )
        if awaiting and not matching_input:
            self.plan.setPlainText("입력이 변경되었습니다. 기존 계획을 취소한 뒤 새 입력으로 계획을 만들어 주세요.")
        elif awaiting and request.plan:
            lines = []
            for step in request.plan.get("steps", []):
                lines.append(
                    f"{step['number']}. {step['description']}\n   {step['tool']} {json.dumps(step['arguments'], ensure_ascii=False)}"
                )
            self.plan.setPlainText("\n".join(lines))
        elif request:
            self.plan.setPlainText(f"v{request.version:03d} · {status_text(request.status)}")
        else:
            self.plan.clear()
        self.versions.clear()
        for artifact in job.artifacts:
            if artifact.stage == "blender":
                self.versions.addItem(
                    f"v{artifact.version:03d} · {artifact.kind.upper()} · {Path(artifact.path).name}"
                )
                self.versions.item(self.versions.count() - 1).setData(256, artifact.path)
        ready = bool(job.blender_input_path)
        self.inspect.setEnabled(ready and not busy)
        self.propose.setEnabled(ready and not busy and not awaiting)
        self.approve.setEnabled(awaiting and matching_input and bool(request.plan) and not busy)
        self.deny.setEnabled(awaiting and not busy)
