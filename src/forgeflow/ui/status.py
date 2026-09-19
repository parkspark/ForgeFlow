from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

STATUS_LABELS = {
    "pending": "대기",
    "running": "진행 중",
    "planning": "계획 중",
    "awaiting_approval": "승인 대기",
    "awaiting_review": "검토 대기",
    "completed": "완료",
    "succeeded": "실행 성공",
    "failed": "실패",
    "cancelled": "취소됨",
    "denied": "승인 거절",
    "approved": "승인됨",
    "accepted": "승인됨",
    "rejected": "반려됨",
    "verified": "검증 완료",
    "unavailable": "검증 정보 없음",
    "partial": "일부 검증",
    "skipped": "건너뜀",
}


def status_text(status: str) -> str:
    return STATUS_LABELS.get(status, "확인 필요")


def set_status_badge(label: QLabel, title: str, status: str) -> None:
    tone = "neutral"
    if status in {"completed", "succeeded", "approved", "accepted", "verified"}:
        tone = "success"
    elif status in {"failed", "denied", "rejected"}:
        tone = "error"
    elif status in {"running", "planning"}:
        tone = "active"
    elif status in {"awaiting_review", "awaiting_approval", "partial"}:
        tone = "warning"
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setMinimumHeight(28)
    label.setText(f"{title} · {status_text(status)}")
    label.setProperty("statusTone", tone)
    label.setAccessibleName(label.text())
    label.style().unpolish(label)
    label.style().polish(label)
    label.update()
