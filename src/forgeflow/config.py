from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path


def _default_blender() -> Path:
    candidates = [
        Path(r"C:\Users\park\Applications\blender-5.2.0-windows-x64\blender.exe"),
        Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"),
    ]
    return next((item for item in candidates if item.is_file()), candidates[0])


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

    def save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: str(value) if isinstance(value, Path) else value for key, value in asdict(self).items()}
        temporary = self.config_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.config_path)

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        default = cls()
        selected = path or default.config_path
        if not selected.is_file():
            return default
        payload = json.loads(selected.read_text(encoding="utf-8"))
        path_fields = {
            "modeling_root", "blender_agent_root", "blender_mcp_root",
            "unity_agent_root", "unity_mcp_root", "blender_executable", "jobs_root",
        }
        values = {key: Path(value) if key in path_fields else value for key, value in payload.items()}
        if values.get("theme", "dark") not in {"light", "dark"}:
            values["theme"] = "dark"
        return cls(**values)

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
