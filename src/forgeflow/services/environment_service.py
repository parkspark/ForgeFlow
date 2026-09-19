from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal

from forgeflow.adapters.blender_adapter import BlenderAdapter
from forgeflow.config import AppConfig
from forgeflow.services.job_service import JobService
from forgeflow.services.process_service import SyncProcessRunner


class EnvironmentWorker(QThread):
    completed = Signal(object)

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config

    def run(self) -> None:
        checks: dict[str, dict[str, Any]] = {}
        checks["modeling"] = self._path_check(
            self.config.modeling_root / "scripts" / "generate_model.ps1", "Pixal3D 생성 브리지"
        )
        checks["agent"] = self._path_check(self.config.agent_python, "Blender 자연어 에이전트")
        checks["mcp_project"] = self._path_check(self.config.mcp_python, "Blender MCP")
        checks["blender"] = self._path_check(self.config.blender_executable, "Blender")
        checks["gpu"] = self._command_check(
            [
                shutil.which("nvidia-smi") or "nvidia-smi",
                "--query-gpu=name",
                "--format=csv,noheader",
            ],
            15,
        )
        checks["wsl"] = self._command_check(
            [
                shutil.which("wsl") or "wsl.exe",
                "-d",
                "Ubuntu-24.04",
                "-u",
                "park",
                "--",
                "printf",
                "ready",
            ],
            20,
        )
        checks["unirig"] = self._unirig_check()
        checks.update(self._ollama_checks())
        checks["unity_mcp"] = self._unity_mcp_check()
        checks["mcp"] = self._blender_mcp_check(
            checks["agent"]["ok"] and checks["mcp_project"]["ok"]
        )
        self.completed.emit(checks)

    def _blender_mcp_check(self, prerequisites_ready: bool) -> dict[str, Any]:
        if not prerequisites_ready:
            return {"ok": False, "detail": "에이전트/MCP 환경 필요"}
        try:
            adapter = BlenderAdapter(self.config, JobService(self.config.jobs_root))
            output: list[str] = []
            code = SyncProcessRunner().run(
                adapter.build_check(self.config.jobs_root),
                lambda _channel, line: output.append(line),
                timeout=120,
            )
            event = next(
                (json.loads(line) for line in reversed(output) if line.startswith("{")), {}
            )
            payload = event.get("payload", {})
            return payload.get(
                "mcp",
                {"ok": code == 0, "detail": "연결 점검 완료" if code == 0 else "연결 실패"},
            )
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

    def _unirig_check(self) -> dict[str, Any]:
        required_commit = "6793c6640ff01c8fb389f3993434124bb43d2933"
        script = self.config.rigging_script.resolve(strict=False)
        missing: list[str] = []
        if not script.is_file():
            missing.append(f"rig_humanoid.ps1: {script}")
        wsl = shutil.which("wsl") or shutil.which("wsl.exe")
        if not wsl:
            missing.append("wsl.exe")
            return {"ok": False, "detail": "누락: " + ", ".join(missing)}
        command = (
            "set -e; "
            "test -x /home/park/miniforge3/envs/unirig/bin/python; "
            "test -d /home/park/local-modeling/UniRig; "
            "test -f /home/park/local-modeling/UniRig/src/model/sdpa_mha.py; "
            "test -d /home/park/.cache/huggingface/hub/models--VAST-AI--UniRig; "
            "git -C /home/park/local-modeling/UniRig rev-parse HEAD"
        )
        try:
            result = subprocess.run(
                [wsl, "-d", "Ubuntu-24.04", "-u", "park", "--", "bash", "-lc", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
            commit = lines[-1] if result.returncode == 0 and lines else None
            if result.returncode != 0:
                missing.append(
                    "Ubuntu-24.04/park UniRig Python, repository, sdpa_mha.py 또는 checkpoint cache"
                )
            detail = "모든 필수 항목 확인"
            if missing:
                detail = "누락/접근 실패: " + "; ".join(missing)
            elif commit != required_commit:
                detail = f"실행 가능 · commit 경고: 실제 {commit}, 확인 기준 {required_commit}"
            else:
                detail = f"실행 가능 · commit {commit}"
            return {
                "ok": not missing,
                "detail": detail,
                "commit": commit,
                "warning": bool(commit and commit != required_commit),
            }
        except Exception as exc:
            missing.append(str(exc))
            return {"ok": False, "detail": "누락/접근 실패: " + "; ".join(missing)}

    @staticmethod
    def _path_check(path: Path, label: str) -> dict[str, Any]:
        return {"ok": path.is_file(), "detail": f"{label}: {path}"}

    @staticmethod
    def _command_check(command: list[str], timeout: int) -> dict[str, Any]:
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            detail = (result.stdout or "").strip().splitlines()
            return {
                "ok": result.returncode == 0,
                "detail": detail[0] if detail else f"종료 코드 {result.returncode}",
            }
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

    def _ollama_checks(self) -> dict[str, dict[str, Any]]:
        # Unity uses ollama.AsyncClient(), whose endpoint comes from OLLAMA_HOST.
        unity_url = os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
        if "://" not in unity_url:
            unity_url = "http://" + unity_url
        results = {}
        endpoints: dict[str, tuple[set[str], str | None]] = {}
        for key, model, base_url in (
            ("ollama_blender", self.config.ollama_model, self.config.ollama_base_url),
            ("ollama_unity", self.config.unity_agent_model, unity_url),
        ):
            base_url = base_url.rstrip("/")
            if base_url not in endpoints:
                try:
                    with urllib.request.urlopen(base_url + "/api/tags", timeout=5) as response:
                        payload = json.load(response)
                    endpoints[base_url] = (
                        {item.get("name") for item in payload.get("models", [])},
                        None,
                    )
                except Exception as exc:
                    endpoints[base_url] = (set(), str(exc))
            names, error = endpoints[base_url]
            tag = model if ":" in model.rsplit("/", 1)[-1] else model + ":latest"
            ok = error is None and (model in names or tag in names)
            status = "서버 연결 실패" if error is not None else ("설치됨" if ok else "모델 없음")
            detail = f"{model}\n{base_url}\n{status}"
            if error is not None:
                detail += f": {error}"
            results[key] = {"ok": ok, "model": model, "status": status, "detail": detail}
        return results

    def _unity_mcp_check(self) -> dict[str, Any]:
        server = self.config.unity_mcp_root / "server.py"
        if not server.is_file():
            return {"ok": False, "detail": f"Unity MCP 서버 없음: {server}"}

        host = os.environ.get("UNITY_MCP_HOST", "127.0.0.1")
        raw_port = os.environ.get("UNITY_MCP_PORT", "8722")
        try:
            port = int(raw_port)
        except ValueError:
            return {"ok": False, "detail": f"UNITY_MCP_PORT가 올바르지 않음: {raw_port}"}

        request = json.dumps({"command": "ping", "params": {}}).encode("utf-8") + b"\n"
        try:
            with socket.create_connection((host, port), timeout=3.0) as connection:
                connection.settimeout(3.0)
                connection.sendall(request)
                response = bytearray()
                while b"\n" not in response:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    response.extend(chunk)
                    if len(response) > 1024 * 1024:
                        raise ValueError("Unity MCP ping 응답이 너무 큽니다.")
        except (OSError, TimeoutError) as exc:
            return {
                "ok": False,
                "detail": f"서버 확인됨 · Unity Editor Bridge 연결 실패({host}:{port}): {exc}",
            }

        if not response:
            return {"ok": False, "detail": f"Unity Editor Bridge의 ping 응답이 없음: {host}:{port}"}
        try:
            payload = json.loads(bytes(response).split(b"\n", 1)[0].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {"ok": False, "detail": f"Unity MCP ping 응답 형식 오류: {exc}"}
        if payload.get("status") != "ok" or not isinstance(payload.get("result"), dict):
            return {"ok": False, "detail": str(payload.get("error") or "Unity MCP ping 실패")}

        result = payload["result"]
        product = result.get("productName") or "Unity Editor"
        version = result.get("unityVersion") or "버전 미상"
        actual_port = result.get("port", port)
        project = result.get("projectPath")
        detail = f"{product} · Unity {version} · {host}:{actual_port}"
        if project:
            detail += f"\n프로젝트: {project}"
        return {"ok": True, "detail": detail, "project_path": project, "port": actual_port}
