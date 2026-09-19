from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from forgeflow.adapters.unity_adapter import UnityAdapter
from forgeflow.domain.job import UnitySession, UnityTurn
from forgeflow.services.job_service import JobService, sha256_file


class FakeProcess:
    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.pid = 123
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = -1
        return self.returncode


@pytest.fixture
def unity_project(tmp_path: Path) -> Path:
    project = tmp_path / "Unity Project"
    (project / "Assets").mkdir(parents=True)
    (project / "ProjectSettings").mkdir()
    return project


def _adapter(config, image, unity_project):
    jobs = JobService(config.jobs_root)
    job = jobs.create("unity", image)
    adapter = UnityAdapter(config, jobs)
    adapter.job = job
    adapter.session = UnitySession("session-test", str(unity_project), status="ready")
    adapter.process = FakeProcess()
    root = jobs.job_directory(job.job_id) / "unity" / "sessions" / "session-test"
    (root / "turns").mkdir(parents=True)
    (root / "screenshots").mkdir()
    adapter._session_dir = root
    adapter._session_log = root / "session.jsonl"
    return jobs, job, adapter


def test_schema_v2_migrates_to_v3_with_unity_stage(config, image):
    jobs = JobService(config.jobs_root)
    job = jobs.create("old", image)
    path = jobs.job_directory(job.job_id) / "job.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = 2
    payload["stages"].pop("unity")
    for key in (
        "unity_project_path",
        "unity_asset_path",
        "unity_sessions",
        "unity_turns",
        "latest_unity_scene_path",
        "latest_unity_screenshot_path",
    ):
        payload.pop(key, None)
    path.write_text(json.dumps(payload), encoding="utf-8")
    restored = jobs.load(job.job_id)
    assert restored.schema_version == 3
    assert restored.stages["unity"].status == "pending"
    assert restored.unity_sessions == []
    assert restored.unity_turns == []


def test_unity_project_validation(unity_project, tmp_path):
    assert UnityAdapter.validate_project(unity_project) == unity_project.resolve()
    bad = tmp_path / "bad"
    bad.mkdir()
    with pytest.raises(ValueError, match="Assets"):
        UnityAdapter.validate_project(bad)


def test_command_passes_explicit_project_and_isolated_environment(config, unity_project, tmp_path):
    agent = tmp_path / "unity-agent"
    mcp = tmp_path / "unity-mcp"
    agent.mkdir()
    mcp.mkdir()
    (agent / "main.py").write_text("", encoding="utf-8")
    (mcp / "server.py").write_text("", encoding="utf-8")
    configured = replace(config, unity_agent_root=agent, unity_mcp_root=mcp)
    adapter = UnityAdapter(configured, JobService(config.jobs_root))
    command = adapter.build_session_command(unity_project, tmp_path / "session")
    index = command.arguments.index("--project")
    assert command.arguments[index + 1] == str(unity_project.resolve())
    assert command.environment["UNITY_PROJECT_DIR"] == str(unity_project.resolve())
    assert command.environment["UNITY_AGENT_RUN_LOG_DIR"].endswith("session\\runs")
    assert "--forgeflow-jsonl" in command.arguments


def test_project_identity_mismatch_is_blocked(config, image, unity_project, tmp_path):
    _jobs, _job, adapter = _adapter(config, image, unity_project)
    adapter._handle_event({"type": "session_ready", "projectPath": str(tmp_path / "Other Project")})
    assert adapter.session.status == "failed"
    assert "불일치" in adapter.session.error


def test_unity_session_and_turn_round_trip(config, image, unity_project):
    jobs, job, adapter = _adapter(config, image, unity_project)
    jobs.add_unity_session(job, adapter.session)
    turn = adapter.send_prompt("현재 씬을 분석해줘")
    adapter._handle_event({"type": "assistant_text", "id": turn.turn_id, "text": "씬입니다."})
    adapter._handle_event({"type": "turn_completed", "id": turn.turn_id, "runLogPath": "run.log"})
    restored = jobs.load(job.job_id)
    assert restored.unity_sessions[-1].session_id == "session-test"
    assert restored.unity_turns[-1].assistant_text == "씬입니다."
    assert restored.unity_turns[-1].status == "succeeded"
    assert restored.stages["unity"].status == "awaiting_review"
    assert restored.unity_turns[-1].human_review_status == "pending"


def test_jsonl_event_parse_and_screenshot_registration(config, image, unity_project, tmp_path):
    jobs, job, adapter = _adapter(config, image, unity_project)
    jobs.add_unity_session(job, adapter.session)
    turn = adapter.send_prompt("스크린샷을 찍어줘")
    event = adapter.parse_jsonl_event(
        json.dumps({"type": "screenshot", "id": turn.turn_id, "path": "shot.png"})
    )
    adapter._handle_event(event)
    assert job.latest_unity_screenshot_path.endswith("shot.png")
    assert turn.screenshot_paths[-1].endswith("shot.png")
    with pytest.raises(ValueError):
        adapter.parse_jsonl_event("{}")


def test_receipt_separates_automated_from_human_review(tmp_path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "status": "verified",
                "requested_checks": ["compile"],
                "measured_checks": ["compile"],
                "skipped_checks": [],
                "unmapped_requirements": ["camera feel"],
            }
        ),
        encoding="utf-8",
    )
    parsed = UnityAdapter.parse_receipt(receipt)
    assert parsed["automated_status"] == "partial"
    assert UnityTurn("t", "s", "u", "e").human_review_status == "pending"


def test_empty_checks_are_not_verified(tmp_path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"status": "verified"}), encoding="utf-8")
    assert UnityAdapter.parse_receipt(receipt)["automated_status"] == "unavailable"


def test_changed_assets_are_ordered_and_deduplicated(tmp_path):
    audit = tmp_path / "run.jsonl"
    events = [
        {
            "event": "tool_result",
            "name": "unity_write_script",
            "arguments": {"path": "Assets/Scripts/Player.cs"},
            "result": {"paths": ["Assets/Scripts/Player.cs", "Assets/Scenes/Main.unity"]},
        },
        {
            "event": "tool_result",
            "name": "unity_save_scene",
            "arguments": {"path": "Assets/Scenes/Main.unity"},
            "result": "Assets/Materials/Hero.mat",
        },
    ]
    audit.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

    assert UnityAdapter.collect_changed_assets(audit) == [
        "Assets/Scripts/Player.cs",
        "Assets/Scenes/Main.unity",
        "Assets/Materials/Hero.mat",
    ]


def test_human_accept_and_reject_are_explicit(config, image, unity_project):
    jobs, job, adapter = _adapter(config, image, unity_project)
    turn = UnityTurn("turn-a", "session-test", "x", "x", status="succeeded")
    job.unity_turns.append(turn)
    jobs.save(job)
    adapter.review_turn(turn.turn_id, True, "직접 확인")
    assert turn.human_review_status == "accepted"
    assert job.stages["unity"].status == "completed"
    turn_b = UnityTurn("turn-b", "session-test", "x", "x", status="succeeded")
    job.unity_turns.append(turn_b)
    jobs.save(job)
    adapter.review_turn(turn_b.turn_id, False, "너무 빠름")
    assert turn_b.human_review_status == "rejected"
    assert job.stages["unity"].status == "failed"


def test_fbx_import_versions_without_overwrite(config, image, unity_project, tmp_path):
    jobs = JobService(config.jobs_root)
    job = jobs.create("fbx", image)
    source = tmp_path / "Character.fbx"
    source.write_bytes(b"humanoid-fbx")
    job.unity_input_path = str(source)
    jobs.save(job)
    adapter = UnityAdapter(config, jobs)
    first = adapter.import_humanoid_fbx(job, unity_project)
    second = adapter.import_humanoid_fbx(job, unity_project)
    assert first.asset_path.endswith("v001/Character_humanoid.fbx")
    assert second.asset_path.endswith("v002/Character_humanoid.fbx")
    assert Path(first.absolute_path).read_bytes() == b"humanoid-fbx"
    assert sha256_file(Path(second.absolute_path)) == sha256_file(source)
    assert not Path(first.absolute_path + ".meta").exists()


def test_chat_without_fbx_and_effective_prompt_preserves_user_text(config, image, unity_project):
    _jobs, job, adapter = _adapter(config, image, unity_project)
    original = "현재 씬에 캐릭터를 배치해줘\n원문 유지"
    prompt = adapter.build_effective_prompt(job, unity_project, original)
    assert "Available imported assets:\n(none selected)" in prompt
    assert f"[User Request]\n{original}" in prompt
    assert str(unity_project.resolve()) in prompt


def test_selected_fbx_prompt_requires_exact_version_and_job_output_scope(
    config, image, unity_project
):
    _jobs, job, adapter = _adapter(config, image, unity_project)
    job.unity_asset_path = f"Assets/ForgeFlow/{job.job_id}/Models/v004/Character_humanoid.fbx"
    prompt = adapter.build_effective_prompt(
        job, unity_project, "이 캐릭터를 배치해줘", include_asset=True
    )
    assert "exact required path" in prompt
    assert "Do not search for or substitute another version" in prompt
    assert f"under Assets/ForgeFlow/{job.job_id}/" in prompt


def test_selected_fbx_is_structured_jsonl_policy_context(config, image, unity_project):
    _jobs, job, adapter = _adapter(config, image, unity_project)
    job.unity_asset_path = "Assets/ForgeFlow/job/Models/v005/Character.fbx"
    adapter.send_prompt("새 테스트 씬을 만들어줘", include_asset=True)
    message = json.loads(adapter.process.stdin.getvalue())
    assert message["selected_asset_paths"] == [job.unity_asset_path]
    assert message["preferred_output_root"] == f"Assets/ForgeFlow/{job.job_id}/"
    assert message["human_review_feedback"] is None


def test_repair_prompt_includes_human_feedback(config, image, unity_project):
    jobs, job, adapter = _adapter(config, image, unity_project)
    source = UnityTurn(
        "turn-old",
        "session-test",
        "백덤블링을 추가해줘",
        "effective",
        status="succeeded",
        human_review_status="rejected",
    )
    job.unity_turns.append(source)
    jobs.save(job)
    turn = adapter.send_review_repair(source.turn_id, "회전 시간을 1.2초로 늘려 주세요.")
    assert turn.repair_existing is True
    assert "[Human Review Feedback]" in turn.effective_prompt
    assert "회전 시간을 1.2초" in turn.effective_prompt


def test_prompt_file_fallback_removes_jsonl_flag(config, unity_project, tmp_path):
    agent = tmp_path / "unity-agent"
    mcp = tmp_path / "unity-mcp"
    agent.mkdir()
    mcp.mkdir()
    (agent / "main.py").write_text("", encoding="utf-8")
    (mcp / "server.py").write_text("", encoding="utf-8")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("test", encoding="utf-8")
    adapter = UnityAdapter(
        replace(config, unity_agent_root=agent, unity_mcp_root=mcp),
        JobService(config.jobs_root),
    )
    command = adapter.build_prompt_file_command(
        unity_project, prompt, tmp_path / "session", repair_existing=True
    )
    assert "--forgeflow-jsonl" not in command.arguments
    assert "--prompt-file" in command.arguments
    assert "--repair-existing" in command.arguments
