from __future__ import annotations

import json
from dataclasses import replace

from forgeflow.config import AppConfig
from forgeflow.ui.theme import THEMES, build_stylesheet, normalize_theme


def test_light_and_dark_styles_are_complete_and_distinct():
    assert set(THEMES) == {"light", "dark"}
    light = build_stylesheet("light")
    dark = build_stylesheet("dark")
    assert light != dark
    for stylesheet in (light, dark):
        assert "QPushButton" in stylesheet
        assert "QTabBar::tab:selected" in stylesheet
        assert 'QLabel[envState="ok"]' in stylesheet
        assert "QWidget#unityComposer" in stylesheet
        assert 'QLabel[connectionState="ok"]' in stylesheet
        assert "#2563eb" not in stylesheet.lower()


def test_theme_selection_persists_and_invalid_value_falls_back(config: AppConfig):
    selected = replace(config, theme="light")
    selected.save()
    assert AppConfig.load(selected.config_path).theme == "light"

    payload = json.loads(selected.config_path.read_text(encoding="utf-8"))
    payload["theme"] = "neon"
    selected.config_path.write_text(json.dumps(payload), encoding="utf-8")
    assert AppConfig.load(selected.config_path).theme == "dark"
    assert normalize_theme("unknown") == "dark"


def test_config_location_survives_jobs_root_change(config, tmp_path):
    config.save()
    updated = replace(config, jobs_root=tmp_path / "elsewhere" / "jobs")
    updated.save()
    assert updated.config_path == config.config_path
    assert AppConfig.load().jobs_root == updated.jobs_root
    assert not (updated.jobs_root.parent / "config.json").exists()
