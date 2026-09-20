from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QSize
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QScrollArea

from forgeflow.ui.main_window import MainWindow


def settle(qapp):
    for _ in range(4):
        qapp.processEvents()


def reveal(button, qapp):
    """Follow the scroll route available to a user, including Unity's sidebar."""
    scroll_areas = []
    parent = button.parentWidget()
    while parent is not None:
        if isinstance(parent, QScrollArea):
            scroll_areas.append(parent)
        parent = parent.parentWidget()
    for _ in range(2):
        for area in reversed(scroll_areas):
            area.ensureWidgetVisible(button, 8, 8)
            settle(qapp)


@pytest.mark.parametrize("size", [(1280, 800), (1024, 768), (853, 533)])
@pytest.mark.parametrize("expanded", [False, True])
def test_laptop_window_keeps_tabs_cancel_and_actions_reachable(config, image, qapp, size, expanded):
    # The Windows offscreen plugin does not discover all installed fonts.
    # Register the production fonts so Korean text has representative metrics.
    for name in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf", "malgun.ttf", "malgunbd.ttf"):
        font = Path("C:/Windows/Fonts") / name
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
    window = MainWindow(config, run_environment_checks=False)
    try:
        window.current_job = window.jobs.create("작은 화면 검증", image)
        window._render_job()
        window.show()
        window.progress_panel.on_event({"type": "stage_started", "stage": "modeling"})
        window.progress_panel.details.setChecked(expanded)
        window.environment_toggle.setChecked(expanded)
        window.resize(*size)
        actions = (
            (
                window.modeling_panel,
                [window.modeling_panel.generate, window.next_steps["modeling"].buttons["blender"]],
            ),
            (window.blender_panel, [window.blender_panel.propose, window.blender_panel.approve]),
            (
                window.rigging_panel,
                [window.rigging_panel.run_button, window.next_steps["rigging"].buttons["unity"]],
            ),
            (
                window.unity_panel,
                [window.unity_panel.connect_button, window.unity_panel.send_button],
            ),
        )
        for panel, buttons in actions:
            window._show_panel(panel)
            settle(qapp)
            assert window.size() == QSize(*size)
            assert window.tabs.currentWidget().viewport().height() >= 150
            assert not window.tabs.tabBar().visibleRegion().isEmpty()
            assert (
                window.cancel_button.visibleRegion()
                .boundingRect()
                .contains(window.cancel_button.rect())
            )
            for button in buttons:
                reveal(button, qapp)
                bounds = button.rect().translated(button.mapTo(window, QPoint()))
                assert window.rect().contains(bounds), button.text()
                assert button.visibleRegion().boundingRect().contains(button.rect()), button.text()
    finally:
        window.progress_panel.finish("modeling", True, "검증 완료")
        window.close()
