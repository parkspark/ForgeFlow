from __future__ import annotations

import os
from pathlib import Path

import pytest

from forgeflow.config import AppConfig


@pytest.fixture(scope="session")
def qapp():
    """Keep one offscreen QApplication alive for GUI and process tests."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app


@pytest.fixture
def config(tmp_path: Path, monkeypatch) -> AppConfig:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    modeling = tmp_path / "modeling"
    agent = tmp_path / "agent"
    mcp = tmp_path / "mcp"
    for root in (modeling, agent, mcp):
        root.mkdir()
    return AppConfig(
        modeling_root=modeling,
        blender_agent_root=agent,
        blender_mcp_root=mcp,
        blender_executable=tmp_path / "blender.exe",
        jobs_root=tmp_path / "jobs",
        ollama_model="test-model",
        ollama_base_url="http://127.0.0.1:11434",
    )


@pytest.fixture
def image(tmp_path: Path) -> Path:
    from PySide6.QtGui import QImage

    path = tmp_path / "입력 이미지.PNG"
    pixels = QImage(8, 8, QImage.Format.Format_RGBA8888)
    pixels.fill(0xFF4A90E2)
    assert pixels.save(str(path))
    return path
