from __future__ import annotations

import hashlib

import pytest

from forgeflow.adapters.unity.bridge import (
    AVATAR_TOOLS,
    FRAMING_TOOLS,
    REQUIRED_ANIMATION_TOOLS,
    bridge_compatibility,
    collect_bridge_diagnostics,
    required_tools_for_request,
)


@pytest.mark.parametrize(
    ("version", "ready"),
    [
        ("0.5.0", False),
        ("0.6.0", True),
        ("0.6.1", True),
        ("0.10.0", True),
        ("1.0.0", True),
        ("0.6.0-rc.1", False),
        ("0.6.0+local", True),
        (None, False),
        (6, False),
        ("0.6", False),
        ("unknown", False),
    ],
)
def test_runtime_version_is_required_even_when_tools_are_advertised(tmp_path, version, ready):
    compatibility = bridge_compatibility(
        {
            "bridgeVersion": version,
            "supportedTools": list(REQUIRED_ANIMATION_TOOLS),
            "sourceBridgeVersion": "0.6.0",
        },
        tmp_path,
    )
    assert compatibility.ready is ready
    if not ready:
        assert "0.6.0" in compatibility.guidance
        assert str(tmp_path / "Assets/Editor/UnityMcpBridge.cs") in compatibility.guidance


@pytest.mark.parametrize("tools", [None, "unity_configure_humanoid", {}, [None]])
def test_malformed_tool_metadata_cannot_enable_presets(tmp_path, tools):
    compatibility = bridge_compatibility(
        {
            "bridgeVersion": "0.6.0",
            "supportedTools": tools,
            "capabilities": list(REQUIRED_ANIMATION_TOOLS),
        },
        tmp_path,
    )
    assert not compatibility.ready
    assert not compatibility.tools_reported
    assert compatibility.missing_tools == REQUIRED_ANIMATION_TOOLS


@pytest.mark.parametrize("missing", REQUIRED_ANIMATION_TOOLS)
def test_each_exact_animation_tool_is_required(tmp_path, missing):
    compatibility = bridge_compatibility(
        {
            "bridgeVersion": "0.6.0",
            "supportedTools": [tool for tool in REQUIRED_ANIMATION_TOOLS if tool != missing],
        },
        tmp_path,
    )
    assert not compatibility.ready
    assert compatibility.missing_tools == (missing,)
    assert missing in compatibility.guidance


@pytest.mark.parametrize(
    ("request_text", "tools"),
    [
        ("기존 요청\n선택한 FBX를 Humanoid로 구성하고 재임포트해줘.", AVATAR_TOOLS),
        ("선택한 FBX의 Avatar와 애니메이션 클립을 검사해줘.", REQUIRED_ANIMATION_TOOLS),
        ("unity_frame_character를 호출해줘", FRAMING_TOOLS),
        (
            "unity_configure_humanoid, unity_get_animator_state",
            (
                "unity_configure_humanoid",
                "unity_get_animator_state",
            ),
        ),
        ("현재 씬과 카메라 상태를 알려줘", ()),
        ("Animator와 애니메이션 개념을 설명해줘", ()),
        ("unity_create_animation_clip_extra", ()),
    ],
)
def test_request_detection_is_limited_to_known_presets_and_exact_tools(request_text, tools):
    assert required_tools_for_request(request_text) == tools


@pytest.mark.parametrize(
    "version_source",
    [
        'private const string BridgeVersion = "0.6.0";',
        'bridgeVersion = "0.6.0",',
        '["bridgeVersion"] = "0.6.0",',
    ],
)
def test_source_and_installed_fingerprints_are_independent(tmp_path, version_source):
    mcp = tmp_path / "mcp"
    project = tmp_path / "Project"
    source = mcp / "UnityBridge/UnityMcpBridge.cs"
    installed = project / "Assets/Editor/McpBridge/UnityMcpBridge.cs"
    source.parent.mkdir(parents=True)
    installed.parent.mkdir(parents=True)
    source.write_text(version_source, encoding="utf-8")
    installed.write_bytes(b"older installed Bridge")

    diagnostics = collect_bridge_diagnostics(mcp, project, {})

    assert diagnostics["sourceBridgeVersion"] == "0.6.0"
    assert diagnostics["sourceBridgeSha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert (
        diagnostics["installedBridgeSha256"] == hashlib.sha256(installed.read_bytes()).hexdigest()
    )
    assert diagnostics["installedBridgePath"] == str(installed)
    assert diagnostics["bridgeDiagnosticErrors"] == []


def test_reported_installed_location_is_scoped_and_missing_files_are_diagnostic(tmp_path):
    project = tmp_path / "Project"
    installed = project / "Assets/CustomEditor/UnityMcpBridge.cs"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"custom installation")
    metadata = {"bridgeSourcePath": "Assets/CustomEditor/UnityMcpBridge.cs"}
    diagnostics = collect_bridge_diagnostics(tmp_path / "missing-mcp", project, metadata)
    assert diagnostics["installedBridgePath"] == str(installed)
    assert diagnostics["installedBridgeSha256"]
    assert diagnostics["sourceBridgeVersion"] is None
    assert len(diagnostics["bridgeDiagnosticErrors"]) == 1

    diagnostics = collect_bridge_diagnostics(
        tmp_path / "missing-mcp",
        project,
        {
            "bridgeSourcePath": str(tmp_path / "Other Project/UnityMcpBridge.cs"),
        },
    )
    assert diagnostics["installedBridgePath"] == str(project / "Assets/Editor/UnityMcpBridge.cs")
    assert diagnostics["installedBridgeSha256"] is None
    assert len(diagnostics["bridgeDiagnosticErrors"]) == 2


def test_reported_path_is_not_replaced_by_a_different_installed_copy(tmp_path):
    fallback = tmp_path / "Assets/Editor/UnityMcpBridge.cs"
    fallback.parent.mkdir(parents=True)
    fallback.write_bytes(b"not the active Bridge")
    actual = tmp_path / "Assets/Nested/Editor/UnityMcpBridge.cs"
    diagnostics = collect_bridge_diagnostics(
        tmp_path / "mcp",
        tmp_path,
        {
            "bridgeSourcePath": str(actual),
        },
    )
    assert diagnostics["installedBridgePath"] == str(actual)
    assert diagnostics["installedBridgeSha256"] is None
