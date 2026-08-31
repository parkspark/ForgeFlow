from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from forgeflow.ui.theme import build_stylesheet
from forgeflow.ui.unity_panel import UnityPanel


_APP = QApplication.instance() or QApplication([])
_APP.setQuitOnLastWindowClosed(False)


def test_unity_panel_prioritizes_chat_and_scrolls_secondary_controls():
    panel = UnityPanel()
    panel.resize(1120, 760)
    panel.show()
    _APP.processEvents()

    assert panel.main_splitter.orientation() == Qt.Orientation.Horizontal
    assert panel.sidebar_scroll.widgetResizable() is True
    assert (
        panel.sidebar_scroll.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert panel.main_splitter.sizes()[0] > panel.main_splitter.sizes()[1]
    assert panel.chat.minimumHeight() >= 340
    assert panel.screenshot_preview.isHidden()
    assert panel.send_button.text() == "전송  Ctrl+Enter"

    panel.sidebar_toggle.setChecked(True)
    assert panel.sidebar_scroll.isHidden()
    assert panel.sidebar_toggle.text() == "보조 패널 보기"
    panel.sidebar_toggle.setChecked(False)
    assert panel.sidebar_scroll.isVisible()

    panel.close()


def test_unity_sidebar_does_not_expand_for_long_paths_set_before_show():
    panel = UnityPanel()
    panel.setStyleSheet(build_stylesheet("dark"))
    panel.resize(1120, 760)
    long_path = (
        r"C:\Users\park\Documents\ForgeFlow\jobs\job-20260827-160714-46d8a049"
        r"\rigging\v001\rig_job-20260827-160714-46d8a049_v001_humanoid.fbx"
    )
    panel.fbx_label.setText(long_path)
    panel.asset_label.setText(
        "Assets/ForgeFlow/job-20260827-160714-46d8a049/Models/v006/"
        "Character_humanoid.fbx"
    )
    panel.identity_status.setText(f"실제 프로젝트: ForgeFlow · {long_path}")

    panel.show()
    for _ in range(3):
        _APP.processEvents()

    sidebar_body = panel.sidebar_scroll.widget()
    assert sidebar_body.width() <= panel.sidebar_scroll.viewport().width()
    assert panel.sidebar_scroll.horizontalScrollBar().maximum() == 0

    panel.close()
