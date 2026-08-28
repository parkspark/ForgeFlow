from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from forgeflow.adapters.unity_adapter import UnityAdapter
from forgeflow.config import AppConfig
from forgeflow.services.job_service import JobService


READ_PROMPT = "현재 활성 씬과 주요 GameObject를 분석해서 알려줘."
WRITE_PROMPT = (
    "Assets/ForgeFlow/E2E/TextControl/ 아래에 새 씬을 만들고 Cube, Ground, Camera, "
    "Light를 배치해줘. Play Mode에서 화면을 확인하고 스크린샷을 저장해줘."
)
FBX_PROMPT = (
    "이 Humanoid FBX를 이용해 새 테스트 씬에 캐릭터를 배치하고 카메라에서 "
    "전체 모습이 보이게 해줘."
)


def wait_until(app: QCoreApplication, predicate, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.1)
    raise TimeoutError(f"{label} timed out after {timeout:.0f}s")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="ForgeFlow Unity text-control live E2E")
    parser.add_argument("--project", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--repair-write", action="store_true")
    parser.add_argument("--repair-fbx", action="store_true")
    parser.add_argument("--only", choices=("all", "read", "write", "fbx"), default="all")
    args = parser.parse_args()

    app = QCoreApplication.instance() or QCoreApplication([])
    config = AppConfig.load()
    jobs = JobService(config.jobs_root)
    job = jobs.load(args.job_id)
    adapter = UnityAdapter(config, jobs)
    events: list[dict] = []
    adapter.event_received.connect(lambda event: (events.append(event), print(json.dumps(event, ensure_ascii=False), flush=True)))
    adapter.log_received.connect(lambda line: print(f"[agent stderr] {line}", file=sys.stderr, flush=True))
    adapter.protocol_error.connect(lambda message: print(f"[protocol error] {message}", file=sys.stderr, flush=True))

    evidence_name = "unity-e2e-evidence.json" if args.only == "all" else f"unity-e2e-evidence-{args.only}.json"
    evidence_path = jobs.job_directory(job.job_id) / "unity" / evidence_name
    evidence: dict = {
        "project": str(Path(args.project).resolve()),
        "job_id": job.job_id,
        "started_at": time.time(),
        "runs": [],
    }
    try:
        adapter.start_session(job, args.project)
        wait_until(app, lambda: adapter.ready or (adapter.session and adapter.session.status == "failed"), 180, "session ready")
        if not adapter.ready:
            raise RuntimeError(adapter.session.error if adapter.session else "session failed")

        prompts = []
        if args.only in {"all", "read"}:
            prompts.append(("read", READ_PROMPT, False))
        if not args.read_only and args.only in {"all", "write"}:
            prompts.append(("write", WRITE_PROMPT, False, args.repair_write))
        if not args.read_only and args.only in {"all", "fbx"}:
            if args.repair_fbx:
                if not job.unity_asset_path:
                    raise RuntimeError("repair-fbx requires an existing imported asset")
                evidence["import"] = {"asset_path": job.unity_asset_path, "reused": True}
            else:
                imported = adapter.import_humanoid_fbx(job, args.project)
                evidence["import"] = {
                    "asset_path": imported.asset_path,
                    "source_sha256": imported.source_sha256,
                    "absolute_path": imported.absolute_path,
                }
            prompts.append(("fbx", FBX_PROMPT, True, args.repair_fbx))

        prompts = [item if len(item) == 4 else (*item, False) for item in prompts]
        for name, prompt, include_asset, repair_existing in prompts:
            before = len(events)
            feedback = None
            if name == "fbx" and repair_existing:
                feedback = (
                    "기존 ForgeFlow HumanoidTest 결과를 수정하세요. 선택된 정확한 FBX를 유지하고, "
                    "Character가 편집 모드 씬에 영구 배치되었는지 확인한 뒤 씬을 저장하세요. "
                    "현재 스크린샷에서는 캐릭터가 화면 중앙에 지나치게 작게 보입니다. 전신을 "
                    "자르지 않으면서 화면 높이의 약 60~75%를 차지하도록 카메라 프레이밍을 "
                    "조정하고 카메라 transform을 고정 위치로 씬에 저장하세요. "
                    "컴파일 오류와 콘솔 오류를 확인하고 Play Mode Game 뷰 스크린샷을 저장한 뒤 "
                    "Play Mode를 종료하세요. 임시 자동 실행 스크립트는 남기지 마세요."
                )
            turn = adapter.send_prompt(
                prompt, include_asset=include_asset, include_current_scene=True,
                analyze_screenshot=False, repair_existing=repair_existing,
                human_review_feedback=feedback,
            )
            wait_until(app, lambda: turn.status != "running", args.timeout, f"turn {name}")
            app.processEvents()
            evidence["runs"].append(
                {
                    "name": name,
                    "turn_id": turn.turn_id,
                    "status": turn.status,
                    "assistant_text": turn.assistant_text,
                    "tool_events": [event for event in events[before:] if event.get("type", "").startswith("tool_")],
                    "milestones": [event for event in events[before:] if event.get("type") == "milestone"],
                    "screenshots": turn.screenshot_paths,
                    "receipt_path": turn.receipt_path,
                    "run_log_path": turn.run_log_path,
                    "jsonl_log_path": turn.jsonl_log_path,
                    "automated_status": turn.automated_status,
                    "human_review_status": turn.human_review_status,
                    "changed_assets": turn.changed_assets,
                }
            )
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if turn.status != "succeeded":
                raise RuntimeError(f"{name} failed: {turn.error}")
            if turn.human_review_status != "pending":
                raise RuntimeError("E2E must leave human review pending")
        return 0
    except BaseException as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(evidence["error"], file=sys.stderr)
        return 1
    finally:
        adapter.shutdown()
        evidence["finished_at"] = time.time()
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"EVIDENCE={evidence_path}")


if __name__ == "__main__":
    raise SystemExit(main())
