from __future__ import annotations

import io
import json
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from forgeflow.adapters.unity_adapter import UnityAdapter
from forgeflow.domain.job import UnitySession, UnityTurn
from forgeflow.domain.process import ProcessCommand
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
            "result": {"status": "ok", "result": {"written": "Assets/Scripts/Player.cs"}},
        },
        {
            "event": "tool_result",
            "name": "unity_save_scene",
            "arguments": {"path": "Assets/Scenes/Main.unity"},
            "result": json.dumps(
                {"status": "ok", "result": {"saved": True, "scene": "Assets/Scenes/Main.unity"}}
            ),
        },
        {
            "event": "tool_result",
            "name": "unity_write_script",
            "result": {"status": "ok", "result": {"written": "Assets/Scripts/Player.cs"}},
        },
    ]
    audit.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

    assert UnityAdapter.collect_changed_assets(audit) == [
        "Assets/Scripts/Player.cs",
        "Assets/Scenes/Main.unity",
    ]


@pytest.mark.parametrize("code", [0, 7])
def test_process_exit_without_completion_fails_active_turn(
    config, image, unity_project, monkeypatch, code
):
    jobs, job, adapter = _adapter(config, image, unity_project)
    turn = adapter.send_prompt("현재 씬을 분석해줘")
    monkeypatch.setattr(adapter.process, "wait", lambda: code)

    adapter._wait_process()

    assert turn.status == "failed"
    assert turn.completed_at and turn.error
    assert adapter._active_turn is None
    assert jobs.load(job.job_id).stages["unity"].status == "failed"
    assert turn.human_review_status == "pending"


def test_session_error_fails_active_turn_before_process_exit(
    config, image, unity_project, monkeypatch
):
    jobs, job, adapter = _adapter(config, image, unity_project)
    turn = adapter.send_prompt("현재 씬을 분석해줘")
    adapter._handle_event({"type": "session_error", "error": "bridge disconnected"})

    assert turn.status == "failed"
    assert jobs.load(job.job_id).stages["unity"].error == "bridge disconnected"
    monkeypatch.setattr(adapter.process, "wait", lambda: 1)
    adapter._wait_process()
    adapter._handle_event({"type": "turn_completed", "id": turn.turn_id})
    assert turn.status == "failed"
    assert adapter.session.status == "failed"
    assert adapter._active_turn is None


def test_session_closed_without_completion_fails_active_turn(config, image, unity_project):
    jobs, job, adapter = _adapter(config, image, unity_project)
    turn = adapter.send_prompt("현재 씬을 분석해줘")
    adapter._handle_event({"type": "session_closed"})
    assert turn.status == "failed"
    assert jobs.load(job.job_id).stages["unity"].status == "failed"


def test_process_exit_drains_buffered_completion_before_failing(
    config, image, unity_project, monkeypatch, qapp
):
    _jobs, job, adapter = _adapter(config, image, unity_project)
    turn = adapter.send_prompt("현재 씬을 분석해줘")
    process_exited = threading.Event()
    release_stdout = threading.Event()

    def wait():
        process_exited.set()
        return 0

    def read_buffered_completion():
        if release_stdout.wait(5):
            adapter._handle_event({"type": "turn_completed", "id": turn.turn_id})

    monkeypatch.setattr(adapter.process, "wait", wait)
    reader = threading.Thread(target=read_buffered_completion)
    waiter = threading.Thread(
        target=adapter._wait_process, args=(adapter.process, adapter.session, reader)
    )
    reader.start()
    waiter.start()
    try:
        assert process_exited.wait(5)
        waiter.join(0.05)
        assert waiter.is_alive(), "process finalization must wait for buffered stdout"
        assert turn.status == "running"
    finally:
        release_stdout.set()
        reader.join(5)
        waiter.join(5)
    assert not reader.is_alive() and not waiter.is_alive()
    assert turn.status == "succeeded"
    assert turn.human_review_status == "pending"
    assert job.stages["unity"].status == "awaiting_review"


@pytest.mark.parametrize("already_exited", [False, True])
def test_cancel_is_terminal_even_after_process_exit_or_late_completion(
    config, image, unity_project, monkeypatch, already_exited
):
    jobs, job, adapter = _adapter(config, image, unity_project)
    turn = adapter.send_prompt("새 씬을 만들어줘")
    if already_exited:
        adapter.process.returncode = 0
    monkeypatch.setattr(adapter, "_terminate_tree", lambda process: None)

    adapter.cancel_active()
    adapter._handle_event({"type": "turn_completed", "id": turn.turn_id})
    adapter._wait_process()

    assert turn.status == "cancelled"
    assert adapter._active_turn is None
    assert jobs.load(job.job_id).stages["unity"].status == "cancelled"
    assert turn.human_review_status == "pending"


@pytest.mark.parametrize("completed", [False, True])
def test_shutdown_cancels_running_turn_and_preserves_completed_review(
    config, image, unity_project, completed
):
    jobs, job, adapter = _adapter(config, image, unity_project)
    turn = adapter.send_prompt("새 씬을 만들어줘")
    if completed:
        adapter._handle_event({"type": "turn_completed", "id": turn.turn_id})

    adapter.shutdown(wait_seconds=0)

    assert adapter.process is None
    assert adapter._active_turn is None
    assert turn.status == ("succeeded" if completed else "cancelled")
    assert jobs.load(job.job_id).stages["unity"].status == (
        "awaiting_review" if completed else "cancelled"
    )
    assert turn.human_review_status == "pending"


def test_previous_session_reader_and_exit_cannot_change_new_session(config, image, unity_project):
    _jobs, job, adapter = _adapter(config, image, unity_project)
    old_process, old_session = adapter.process, adapter.session
    old_turn = adapter.send_prompt("이전 명령")
    old_process.stdout = io.StringIO(
        json.dumps({"type": "turn_completed", "id": old_turn.turn_id}) + "\n"
    )
    old_turn.status = "cancelled"
    adapter.process = FakeProcess()
    adapter.session = UnitySession("session-new", str(unity_project), status="ready")
    adapter._active_turn = None
    new_turn = adapter.send_prompt("새 명령")

    adapter._read_stdout(old_process, old_session)
    adapter._process_finished(old_process, old_session, 1)

    assert new_turn.status == "running"
    assert adapter._active_turn is new_turn
    assert adapter.session.status == "ready"
    assert job.stages["unity"].status == "running"


def test_prompt_write_failure_does_not_leave_running_turn(
    config, image, unity_project, monkeypatch
):
    jobs, job, adapter = _adapter(config, image, unity_project)

    def broken_pipe(message):
        raise BrokenPipeError("agent exited")

    monkeypatch.setattr(adapter, "_send", broken_pipe)
    with pytest.raises(BrokenPipeError):
        adapter.send_prompt("현재 씬을 분석해줘")
    assert adapter.session.status == "failed"
    assert job.unity_turns[-1].status == "failed"
    assert jobs.load(job.job_id).stages["unity"].status == "failed"
    assert adapter._active_turn is None


@pytest.mark.parametrize("send_completion", [False, True])
def test_real_jsonl_process_exit_preserves_completion_or_fails_missing_event(
    config, image, unity_project, monkeypatch, qapp, send_completion
):
    jobs = JobService(config.jobs_root)
    job = jobs.create("jsonl-process", image)
    adapter = UnityAdapter(config, jobs)
    program = "\n".join(
        [
            "import json,sys",
            f"print(json.dumps({{'type':'session_ready','projectPath':{str(unity_project)!r}}}),flush=True)",
            "message=json.loads(sys.stdin.readline())",
            (
                "print(json.dumps({'type':'turn_completed','id':message['id']}),flush=True)"
                if send_completion
                else "pass"
            ),
        ]
    )
    monkeypatch.setattr(
        adapter,
        "build_session_command",
        lambda project, session: ProcessCommand(sys.executable, ["-u", "-c", program], project),
    )
    try:
        adapter.start_session(job, unity_project)
        deadline = time.monotonic() + 5
        while not adapter.ready and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert adapter.ready
        turn = adapter.send_prompt("씬을 분석해줘")
        for thread in adapter._reader_threads:
            thread.join(5)
        assert all(not thread.is_alive() for thread in adapter._reader_threads)
        assert turn.status == ("succeeded" if send_completion else "failed")
        assert jobs.load(job.job_id).stages["unity"].status == (
            "awaiting_review" if send_completion else "failed"
        )
        assert turn.human_review_status == "pending"
    finally:
        adapter.shutdown()


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
