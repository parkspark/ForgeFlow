"""Thin JSON bridge executed inside blender-prompt-agent's own virtual environment."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from blender_prompt_agent.agent import BlenderPromptAgent
from blender_prompt_agent.config import AgentSettings
from blender_prompt_agent.mcp_client import BlenderMCPClient
from blender_prompt_agent.schemas import (
    ExecutionPlan,
    PlanStep,
    SessionRecord,
    ToolCall,
    validate_tool_call,
)


def emit(event_type: str, **payload: Any) -> None:
    print(json.dumps({"type": event_type, **payload}, ensure_ascii=False), flush=True)


def canonical_hash(plan: dict[str, Any]) -> str:
    encoded = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


async def propose(args: argparse.Namespace) -> int:
    settings = AgentSettings.load()
    emit("stage_started", stage="blender_plan", message="장면 검사 및 실행 계획 생성 중")
    agent = BlenderPromptAgent(settings)
    attempts: list[dict[str, Any]] = []
    try:
        prompt = args.prompt
        result = await agent.run(prompt, approval_callback=lambda _plan: False)
        attempts.append(
            {"session_id": result.session_id, "status": result.status, "summary": result.summary}
        )
        if result.plan is None and result.status == "completed":
            emit(
                "progress",
                stage="blender_plan",
                message="설명문만 반환되어 구조화 실행 계획을 한 번 다시 요청합니다.",
            )
            prompt = (
                args.prompt + "\n중요: 이전 응답은 설명문으로 끝나 실행 계획이 생성되지 않았다. "
                "변경 요청을 한국어로 설명만 하지 말고, 반드시 필요한 asset.* 도구를 "
                "Ollama tool_calls 형식으로 실제 호출 제안하라. 쓰기는 아직 실행되지 않고 승인 대기 계획으로만 저장된다."
            )
            result = await agent.run(prompt, approval_callback=lambda _plan: False)
            attempts.append(
                {
                    "session_id": result.session_id,
                    "status": result.status,
                    "summary": result.summary,
                }
            )
    finally:
        await agent.aclose()
    payload = result.model_dump(mode="json")
    envelope = {"schema_version": 1, "prompt": args.prompt, "attempts": attempts, "result": payload}
    if result.plan is not None:
        plan_value = result.plan.model_dump(mode="json")
        envelope["plan_sha256"] = canonical_hash(plan_value)
    atomic_json(args.output.resolve(), envelope)
    emit("result", stage="blender_plan", payload=envelope)
    return 0 if result.plan is not None and result.status == "denied" else 2


def validated_plan(raw: dict[str, Any]) -> ExecutionPlan:
    parsed = ExecutionPlan.model_validate(raw)
    steps: list[PlanStep] = []
    for step in parsed.steps:
        call = validate_tool_call(ToolCall(name=step.tool, arguments=deepcopy(step.arguments)))
        steps.append(
            PlanStep(
                number=step.number,
                tool=call.name,
                arguments=call.arguments,
                description=step.description,
            )
        )
    return ExecutionPlan(steps=steps, requires_approval=True)


async def execute(args: argparse.Namespace) -> int:
    envelope = json.loads(args.proposal.resolve(strict=True).read_text(encoding="utf-8"))
    raw_plan = envelope.get("result", {}).get("plan")
    if not isinstance(raw_plan, dict):
        raise ValueError("제안 파일에 실행 계획이 없습니다.")
    digest = canonical_hash(raw_plan)
    if digest != args.expected_plan_sha256 or digest != envelope.get("plan_sha256"):
        raise ValueError("승인된 계획의 해시가 일치하지 않습니다.")
    plan = validated_plan(raw_plan)
    expected_input = args.input.resolve(strict=True)
    output_root = args.output_root.resolve(strict=False)
    for step in plan.steps:
        if Path(step.arguments["input_path"]).resolve(strict=True) != expected_input:
            raise ValueError("계획의 입력 경로가 승인된 원본과 다릅니다.")
        if Path(step.arguments["output_directory"]).resolve(strict=False) != output_root:
            raise ValueError("계획의 출력 경로가 승인된 버전 폴더와 다릅니다.")

    settings = AgentSettings.load()
    record = SessionRecord(
        original_prompt=str(envelope.get("prompt", "")),
        model=settings.ollama_model,
        ollama_base_url=settings.ollama_base_url,
        temperature=settings.ollama_temperature,
        plan=plan,
        approved=True,
    )
    record.log_path = settings.session_log_dir.resolve(strict=False) / f"{record.session_id}.json"
    agent = BlenderPromptAgent(settings)
    emit("stage_started", stage="blender", message="승인된 MCP 계획 실행 중")
    try:
        async with BlenderMCPClient(settings) as mcp:
            agent._verified_tools(await mcp.list_tools())
            result = await agent._execute_plan(mcp, record, plan)
    finally:
        await agent.aclose()
    payload = result.model_dump(mode="json")
    atomic_json(
        args.output.resolve(), {"schema_version": 1, "plan_sha256": digest, "result": payload}
    )
    emit("result", stage="blender", payload=payload)
    return 0 if result.status == "completed" else 1


async def inspect_asset(args: argparse.Namespace) -> int:
    settings = AgentSettings.load()
    emit("stage_started", stage="inspect", message="Blender 장면 검사 중")
    async with BlenderMCPClient(settings) as mcp:
        result = await mcp.call_tool(
            "scene.inspect", {"input_path": str(args.input.resolve(strict=True))}
        )
    payload = result.model_dump(mode="json")
    if args.output:
        atomic_json(args.output.resolve(), payload)
    emit("result", stage="inspect", payload=payload)
    return 0 if result.success else 1


async def check_environment(_args: argparse.Namespace) -> int:
    settings = AgentSettings.load()
    agent = BlenderPromptAgent(settings)
    checks: dict[str, Any] = {}
    try:
        await agent.ollama.check_model()
        checks["ollama"] = {"ok": True, "detail": settings.ollama_model}
        async with BlenderMCPClient(settings) as mcp:
            tools = await mcp.list_tools()
            checks["mcp"] = {"ok": True, "detail": f"{len(tools)}개 도구"}
    except Exception as exc:
        checks.setdefault("ollama", {"ok": False, "detail": str(exc)})
        checks.setdefault("mcp", {"ok": False, "detail": str(exc)})
    finally:
        await agent.aclose()
    emit("result", stage="environment", payload=checks)
    return 0 if all(value["ok"] for value in checks.values()) else 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="mode", required=True)
    proposal = sub.add_parser("propose")
    proposal.add_argument("--prompt", required=True)
    proposal.add_argument("--output", type=Path, required=True)
    execution = sub.add_parser("execute")
    execution.add_argument("--proposal", type=Path, required=True)
    execution.add_argument("--expected-plan-sha256", required=True)
    execution.add_argument("--input", type=Path, required=True)
    execution.add_argument("--output-root", type=Path, required=True)
    execution.add_argument("--output", type=Path, required=True)
    inspection = sub.add_parser("inspect")
    inspection.add_argument("--input", type=Path, required=True)
    inspection.add_argument("--output", type=Path)
    sub.add_parser("check")
    return root


async def async_main() -> int:
    args = parser().parse_args()
    handlers = {
        "propose": propose,
        "execute": execute,
        "inspect": inspect_asset,
        "check": check_environment,
    }
    try:
        return await handlers[args.mode](args)
    except Exception as exc:
        emit("error", stage=args.mode, message=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
