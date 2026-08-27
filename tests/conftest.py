from __future__ import annotations

from pathlib import Path

import pytest

from forgeflow.config import AppConfig


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
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
    path = tmp_path / "입력 이미지.PNG"
    path.write_bytes(b"fake-png-content")
    return path

