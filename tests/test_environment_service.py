from __future__ import annotations

import json
from dataclasses import replace

from forgeflow.services.environment_service import EnvironmentWorker


class FakeSocket:
    def __init__(self, payload: dict):
        self.response = json.dumps(payload).encode("utf-8") + b"\n"
        self.request = b""

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        return False

    def settimeout(self, _timeout):
        pass

    def sendall(self, request: bytes):
        self.request = request

    def recv(self, _size: int) -> bytes:
        response, self.response = self.response, b""
        return response


def test_unity_mcp_check_pings_editor_bridge(config, tmp_path, monkeypatch):
    mcp = tmp_path / "unity-mcp"
    mcp.mkdir()
    (mcp / "server.py").write_text("", encoding="utf-8")
    worker = EnvironmentWorker(replace(config, unity_mcp_root=mcp))
    connection = FakeSocket(
        {
            "status": "ok",
            "result": {
                "bridgeVersion": "0.3.0",
                "unityVersion": "6000.1.0f1",
                "productName": "Demo",
                "projectPath": r"C:\Projects\Demo",
                "port": 8722,
            },
        }
    )
    monkeypatch.delenv("UNITY_MCP_HOST", raising=False)
    monkeypatch.delenv("UNITY_MCP_PORT", raising=False)
    monkeypatch.setattr(
        "forgeflow.services.environment_service.socket.create_connection",
        lambda address, timeout: connection,
    )

    result = worker._unity_mcp_check()

    assert result["ok"] is True
    assert result["project_path"] == r"C:\Projects\Demo"
    assert "Demo" in result["detail"]
    assert json.loads(connection.request) == {"command": "ping", "params": {}}


def test_unity_mcp_check_reports_bridge_connection_failure(config, tmp_path, monkeypatch):
    mcp = tmp_path / "unity-mcp"
    mcp.mkdir()
    (mcp / "server.py").write_text("", encoding="utf-8")
    worker = EnvironmentWorker(replace(config, unity_mcp_root=mcp))

    def refuse_connection(_address, timeout):
        raise ConnectionRefusedError("not listening")

    monkeypatch.setattr(
        "forgeflow.services.environment_service.socket.create_connection", refuse_connection
    )

    result = worker._unity_mcp_check()

    assert result["ok"] is False
    assert "Bridge 연결 실패" in result["detail"]


def test_unity_mcp_check_requires_server(config, tmp_path):
    worker = EnvironmentWorker(replace(config, unity_mcp_root=tmp_path / "missing"))

    result = worker._unity_mcp_check()

    assert result["ok"] is False
    assert "서버 없음" in result["detail"]
