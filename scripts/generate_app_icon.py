from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPen


def generate_icon(destination: Path) -> None:
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    background = QLinearGradient(28, 20, 228, 236)
    background.setColorAt(0.0, QColor("#172554"))
    background.setColorAt(1.0, QColor("#0f766e"))
    painter.setBrush(background)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(12, 12, 232, 232), 54, 54)

    flow_pen = QPen(QColor("#ecfeff"), 20, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(flow_pen)
    painter.drawLine(QPointF(72, 62), QPointF(72, 194))
    painter.drawLine(QPointF(72, 62), QPointF(190, 62))
    painter.drawLine(QPointF(72, 126), QPointF(158, 126))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#fbbf24"))
    for point in (QPointF(190, 62), QPointF(158, 126), QPointF(72, 194)):
        painter.drawEllipse(point, 14, 14)

    painter.end()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(destination), "ICO"):
        raise RuntimeError(f"아이콘을 저장하지 못했습니다: {destination}")


if __name__ == "__main__":
    app = QGuiApplication(sys.argv[:1])
    target = Path(__file__).resolve().parents[1] / "src" / "forgeflow" / "resources" / "forgeflow.ico"
    generate_icon(target)
    print(target)
