from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

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
