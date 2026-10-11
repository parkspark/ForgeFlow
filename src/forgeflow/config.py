from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4


def _default_blender() -> Path:
    candidates = [
        Path(r"C:\Users\park\Applications\blender-5.2.0-windows-x64\blender.exe"),
        Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"),
    ]
    return next((item for item in candidates if item.is_file()), candidates[0])


def _atomic_write(path: Path, data: bytes) -> None:
    """Keep readers on complete files and give simultaneous writers separate staging files."""
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _valid_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return (
            url.scheme in {"http", "https"}
            and bool(url.hostname)
            and url.username is None
            and url.password is None
            and not url.query
            and not url.fragment
            and not any(character.isspace() for character in value)
            and "\\" not in value
            and not url.netloc.endswith(":")
            and (url.port is None or 1 <= url.port <= 65535)
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class AppConfig:
    modeling_root: Path = Path(r"C:\Users\park\Desktop\dev_tool\modeling_local_mcp")
    blender_agent_root: Path = Path(r"C:\Users\park\Desktop\dev_tool\blender-prompt-agent")
    blender_mcp_root: Path = Path(r"C:\Users\park\Desktop\dev_tool\blender-control-mcp")
    unity_agent_root: Path = Path(r"C:\Users\park\Desktop\dev_tool\unity_local_mcp")
    unity_mcp_root: Path = Path(r"C:\Users\park\Desktop\dev_tool\unity_mcp")
    blender_executable: Path = _default_blender()
    jobs_root: Path = Path.home() / "Documents" / "ForgeFlow" / "jobs"
    ollama_model: str = "qwen3-coder:30b"
    unity_agent_model: str = "qwen3.8:27b-mtp-q4_K_M"
    ollama_base_url: str = "http://127.0.0.1:11434"
    theme: str = "dark"

    @property
    def rigging_script(self) -> Path:
        return self.modeling_root / "scripts" / "rig_humanoid.ps1"

    @property
    def rigging_staging_root(self) -> Path:
        local_app_data = os.environ.get("LOCALAPPDATA")
        root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return root / "ForgeFlow" / "rigging-staging"

    @property
    def agent_python(self) -> Path:
        return self.blender_agent_root / ".venv" / "Scripts" / "python.exe"

    @property
    def mcp_python(self) -> Path:
        return self.blender_mcp_root / ".venv" / "Scripts" / "python.exe"

    @property
    def powershell(self) -> str:
        return shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"

    @property
    def config_path(self) -> Path:
        return Path.home() / "Documents" / "ForgeFlow" / "config.json"

    def save(self, path: Path | None = None) -> None:
        selected = path or self.config_path
        selected.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            key: str(value) if isinstance(value, Path) else value
            for key, value in asdict(self).items()
        }
        data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        try:
            previous = selected.read_bytes()
        except FileNotFoundError:
            previous = None
        if previous is not None:
            values, problems = self._decode_settings(previous, type(self)())
            if values is not None and not problems:
                backup = self._backup_path(selected)
            else:
                backup = selected.with_name(f"{selected.name}.corrupt-{uuid4().hex}.bak")
            # A failed backup must leave the original untouched.
            _atomic_write(backup, previous)
        _atomic_write(selected, data)

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        config, _warnings = cls.load_with_diagnostics(path)
        return config

    @staticmethod
    def _backup_path(path: Path) -> Path:
        return path.with_name(path.name + ".bak")

    @classmethod
    def _decode_settings(
        cls, data: bytes, default: "AppConfig"
    ) -> tuple[dict[str, Any] | None, list[str]]:
        try:
            payload = json.loads(data)
        except (ValueError, RecursionError) as exc:
            return None, [f"설정 파일의 JSON을 읽을 수 없습니다: {exc}"]
        if not isinstance(payload, dict):
            return None, ["설정 파일은 JSON 객체여야 합니다."]
        values: dict[str, Any] = {}
        problems = []
        # Unknown fields belong to newer versions and are safe to ignore.
        for key, default_value in asdict(default).items():
            if key not in payload:
                continue
            value = payload[key]
            valid = isinstance(value, str) and bool(value.strip()) and "\x00" not in value
            if valid and isinstance(default_value, Path):
                valid = Path(value).is_absolute()
            elif valid and key == "theme":
                valid = value in {"light", "dark"}
            elif valid and key == "ollama_base_url":
                valid = _valid_url(value)
            elif valid and key in {"ollama_model", "unity_agent_model"}:
                valid = not any(character.isspace() for character in value)
            if not valid:
                problems.append(f"설정 항목 {key}의 값이 올바르지 않아 복구했습니다.")
                continue
            values[key] = Path(value) if isinstance(default_value, Path) else value
        return values, problems

    @classmethod
    def _read_settings(
        cls, path: Path, default: "AppConfig"
    ) -> tuple[dict[str, Any] | None, list[str]]:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None, []
        except OSError as exc:
            return None, [f"설정 파일을 읽을 수 없습니다({path}): {exc}"]
        return cls._decode_settings(data, default)

    @classmethod
    def load_with_diagnostics(cls, path: Path | None = None) -> tuple["AppConfig", list[str]]:
        """Recover usable settings in memory, preserving the source until an explicit save."""
        default = cls()
        selected = path or default.config_path
        values, warnings = cls._read_settings(selected, default)
        if values is not None and not warnings:
            return cls(**values), []
        backup_path = cls._backup_path(selected)
        backup, backup_warnings = cls._read_settings(backup_path, default)
        restored = asdict(default)
        if backup is not None and not backup_warnings:
            restored.update(backup)
            warnings.append(
                f"마지막 정상 설정 백업({backup_path})을 사용해 복구했습니다. "
                "현재 파일의 올바른 항목과 원본 파일은 보존했습니다."
            )
        elif warnings:
            warnings.append(
                f"올바르지 않은 설정은 기본값을 사용합니다. 원본은 보존했습니다: {selected}"
            )
            if backup_warnings:
                warnings.append(f"정상 설정 백업도 읽을 수 없어 사용하지 않았습니다: {backup_path}")
        if values is not None:
            restored.update(values)
        return cls(**restored), warnings

    def agent_environment(self, session_dir: Path) -> dict[str, str]:
        environment = dict(os.environ)
        environment.update(
            {
                "OLLAMA_BASE_URL": self.ollama_base_url,
                "OLLAMA_MODEL": self.ollama_model,
                "BLENDER_EXECUTABLE": str(self.blender_executable),
                "BLENDER_MCP_COMMAND": str(self.mcp_python),
                "BLENDER_MCP_ARGS": json.dumps(["-m", "blender_control_mcp.server"]),
                "BLENDER_MCP_CWD": str(self.blender_mcp_root),
                "BLENDER_PROMPT_AGENT_LOG_DIR": str(session_dir),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        return environment

    def unity_environment(self, project_path: Path, session_dir: Path) -> dict[str, str]:
        """Build an isolated environment for one ForgeFlow-owned Unity session."""
        project = project_path.expanduser().resolve(strict=False)
        root = session_dir.expanduser().resolve(strict=False)
        environment = dict(os.environ)
        environment.update(
            {
                "UNITY_PROJECT_DIR": str(project),
                "UNITY_MCP_DIR": str(self.unity_mcp_root.resolve(strict=False)),
                "UNITY_AGENT_RUN_LOG_DIR": str(root / "runs"),
                "UNITY_MCP_AUDIT_LOG_DIR": str(root / "mcp-audit"),
                "UNITY_AGENT_RECEIPT_DIR": str(root / "receipts"),
                "UNITY_AGENT_MODEL": self.unity_agent_model,
                "UNITY_AGENT_AUTO_OPEN": "0",
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        return environment
