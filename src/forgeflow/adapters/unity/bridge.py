"""Bridge handshake policy shared by the JSONL adapter and Unity panel."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

MINIMUM_ANIMATION_BRIDGE_VERSION = "0.6.0"
AVATAR_TOOLS = ("unity_configure_humanoid",)
CLIP_TOOLS = ("unity_get_animation_assets", "unity_create_animation_clip")
CONTROLLER_TOOLS = ("unity_create_animator_controller", "unity_get_animator_state")
REQUIRED_ANIMATION_TOOLS = (*AVATAR_TOOLS, *CLIP_TOOLS, *CONTROLLER_TOOLS)
FRAMING_TOOLS = ("unity_frame_character",)
BRIDGE_TOOL_GROUPS = {"Avatar": AVATAR_TOOLS, "Clip": CLIP_TOOLS, "Controller": CONTROLLER_TOOLS}
_VERSION = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?"
)


def _version_ready(value: Any) -> bool:
    match = _VERSION.fullmatch(value) if isinstance(value, str) else None
    if not match:
        return False
    version = tuple(int(part) for part in match.group(1, 2, 3))
    minimum = tuple(int(part) for part in MINIMUM_ANIMATION_BRIDGE_VERSION.split("."))
    return version > minimum or (version == minimum and not match.group(4))


@dataclass(frozen=True)
class BridgeCompatibility:
    current_version: str
    version_ready: bool
    tools_reported: bool
    missing_tools: tuple[str, ...]
    install_path: str

    @property
    def ready(self) -> bool:
        return self.version_ready and self.tools_reported and not self.missing_tools

    @property
    def reason(self) -> str:
        reasons = []
        if not self.version_ready:
            reasons.append(
                f"Bridge 버전 {self.current_version}: {MINIMUM_ANIMATION_BRIDGE_VERSION} 이상 필요"
            )
        if not self.tools_reported:
            reasons.append("지원 도구 목록(supportedTools)을 확인할 수 없습니다")
        if self.missing_tools:
            reasons.append("필수 도구 누락: " + ", ".join(self.missing_tools))
        return "; ".join(reasons)

    @property
    def guidance(self) -> str:
        if self.ready:
            return ""
        return (
            f"{self.reason}\nBridge 설치 경로: {self.install_path}\n"
            "Bridge를 갱신하고 Unity 컴파일 완료 후 다시 연결하세요. 일반 채팅은 사용할 수 있습니다."
        )


def bridge_compatibility(
    identity: Mapping[str, Any] | None,
    project_path: str | Path,
    required_tools: tuple[str, ...] = REQUIRED_ANIMATION_TOOLS,
) -> BridgeCompatibility:
    identity = identity or {}
    version = identity.get("bridgeVersion")
    tools = identity.get("supportedTools")
    reported = isinstance(tools, list) and all(isinstance(tool, str) for tool in tools)
    supported = set(tools) if reported else set()
    return BridgeCompatibility(
        current_version=version if isinstance(version, str) and version else "미확인",
        version_ready=_version_ready(version),
        tools_reported=reported,
        missing_tools=tuple(tool for tool in required_tools if tool not in supported),
        install_path=str(
            identity.get("installedBridgePath")
            or (Path(project_path).resolve(strict=False) / "Assets/Editor/UnityMcpBridge.cs")
        ),
    )


def required_tools_for_request(text: str) -> tuple[str, ...]:
    """Recognize our presets and explicit tool names, not general animation discussion.

    Old saved preset text is recognized too, including when a user appends it to
    a draft. Arbitrary natural-language requests remain the agent's responsibility.
    """
    explicit = set(re.findall(r"(?<![A-Za-z0-9_])unity_[a-z0-9_]+(?![A-Za-z0-9_])", text))
    normalized = " ".join(text.split()).casefold()
    if "선택한 fbx를 humanoid로 구성하고 재임포트해줘" in normalized:
        explicit.update(AVATAR_TOOLS)
    if "선택한 fbx의 avatar와 애니메이션 클립을 검사해줘" in normalized:
        explicit.update(REQUIRED_ANIMATION_TOOLS)
    return tuple(tool for tool in (*REQUIRED_ANIMATION_TOOLS, *FRAMING_TOOLS) if tool in explicit)


def request_bridge_problem(
    identity: Mapping[str, Any] | None, project_path: str | Path, *requests: str
) -> str:
    required = tuple(
        dict.fromkeys(tool for request in requests for tool in required_tools_for_request(request))
    )
    return bridge_compatibility(identity, project_path, required).guidance if required else ""


def collect_bridge_diagnostics(
    mcp_root: Path, project_path: str | Path, event: Mapping[str, Any]
) -> dict[str, Any]:
    """Read only the configured source and reported/known installed Bridge files.

    Disk fingerprints are diagnostics, never evidence that the running Editor
    supports a tool. Only its session_ready handshake can enable a preset.
    """
    source = (mcp_root / "UnityBridge/UnityMcpBridge.cs").resolve(strict=False)
    project = Path(project_path).resolve(strict=False)
    candidates = [
        project / "Assets/Editor/UnityMcpBridge.cs",
        project / "Assets/Editor/McpBridge/UnityMcpBridge.cs",
    ]
    installed = next((path for path in candidates if path.is_file()), candidates[0])
    reported = event.get("bridgeSourcePath")
    if isinstance(reported, str) and reported:
        path = (project / reported).resolve(strict=False)
        if path.is_relative_to(project) and path.name == "UnityMcpBridge.cs":
            # A reported location remains authoritative even if its file was
            # removed after the Editor loaded it. Never hash a different copy.
            installed = path
    result: dict[str, Any] = {
        "sourceBridgePath": str(source),
        "sourceBridgeVersion": None,
        "sourceBridgeSha256": None,
        "installedBridgePath": str(installed),
        "installedBridgeSha256": None,
    }
    errors = []
    for label, path in (("source", source), ("installed", installed)):
        try:
            data = path.read_bytes()
        except OSError as exc:
            errors.append(f"{label}: {path}: {exc}")
            continue
        result[f"{label}BridgeSha256"] = hashlib.sha256(data).hexdigest()
        if label == "source":
            match = re.search(
                r'\b(?:BridgeVersion|bridgeVersion)\s*=\s*"([^"]+)"'
                r'|\["bridgeVersion"\]\s*=\s*"([^"]+)"',
                data.decode("utf-8-sig", errors="replace"),
            )
            if match:
                result["sourceBridgeVersion"] = match.group(1) or match.group(2)
    result["bridgeDiagnosticErrors"] = errors
    return result
