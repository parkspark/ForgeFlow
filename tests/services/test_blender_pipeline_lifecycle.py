from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal

from forgeflow.adapters.blender_adapter import BlenderAdapter, plan_hash
from forgeflow.domain.artifact import Artifact
from forgeflow.domain.job import BlenderRequest, utc_now
from forgeflow.services.job_service import JobService, sha256_file
from forgeflow.services.pipeline_service import PipelineService


class ControlledProcess(QObject):
    finished = Signal(int, object)
    failed_to_start = Signal(str)

    def __init__(self, pipeline):
        super().__init__(pipeline)
        self.running = False
        self.commands = []
        self.finished.connect(pipeline._on_finished)
        self.failed_to_start.connect(pipeline._on_start_error)

    def start(self, command):
        self.commands.append(command)
        self.running = True

    def finish(self, exit_code=0):
        self.running = False
        self.finished.emit(exit_code, None)

    def fail_to_start(self):
        self.running = False
        self.failed_to_start.emit("controlled executable unavailable")

    def cancel(self):
        # The process may finish successfully while cancellation is in progress.
        self.finish(0)

    def flush(self):
        pass


@pytest.fixture
def blender_pipeline(config, image, qapp):
    config.agent_python.parent.mkdir(parents=True)
    config.agent_python.touch()
    jobs = JobService(config.jobs_root)
    job = jobs.create("Blender lifecycle", image)
    source = jobs.job_directory(job.job_id) / "modeling" / "source.blend"
    source.write_bytes(b"preserved Blender input")
    job.blender_input_path = str(source)
    jobs.add_artifact(
        job, Artifact("blend", str(source), "modeling", utc_now(), sha256=sha256_file(source))
    )
    jobs.set_stage(job, "blender", "completed")
    pipeline = PipelineService(jobs, None, BlenderAdapter(config, jobs), None)
    process = ControlledProcess(pipeline)
    pipeline.process = process
    yield pipeline, process, jobs, job
    pipeline._close_log()


def start_operation(pipeline, process, job, operation):
    pipeline.start_proposal(job, "BLEND로 저장")
    request = job.latest_blender_request
    plan = {
        "steps": [
            {
                "number": 1,
                "tool": "asset.export",
                "arguments": {
                    "input_path": request.input_path,
                    "output_directory": request.output_directory,
                    "formats": ["blend"],
                },
                "description": "BLEND로 저장",
            }
        ]
    }
    Path(request.proposal_path).write_text(
        json.dumps({"plan_sha256": plan_hash(plan), "result": {"status": "denied", "plan": plan}}),
        encoding="utf-8",
    )
    if operation == "blender":
        process.finish()
        assert request.status == "awaiting_approval"
        pipeline.approve(job)
    return request


def write_execution(request):
    output = Path(request.output_directory) / "edited.blend"
    output.write_bytes(b"edited Blender result")
    (output.parent / "execution.json").write_text(
        json.dumps(
            {
                "plan_sha256": request.plan_sha256,
                "result": {"status": "completed", "artifacts": [str(output)]},
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("operation", ["blender_plan", "blender"])
@pytest.mark.parametrize("interruption", ["cancel", "failed_start"])
def test_interruption_persists_blender_request_and_notifies_ui(
    blender_pipeline, operation, interruption
):
    pipeline, process, jobs, job = blender_pipeline
    request = start_operation(pipeline, process, job, operation)
    previous_artifacts = job.to_dict()["artifacts"]
    changes = []
    finished = []
    pipeline.job_changed.connect(lambda current: changes.append(current.job_id))
    pipeline.operation_finished.connect(lambda *args: finished.append(args))
    if operation == "blender":
        write_execution(request)

    if interruption == "cancel":
        pipeline.cancel_active()
    else:
        process.fail_to_start()

    expected = "cancelled" if interruption == "cancel" else "failed"
    restored = jobs.load(job.job_id)
    assert restored.latest_blender_request.status == expected
    assert restored.stages["blender"].status == (
        "completed" if operation == "blender_plan" else expected
    )
    assert restored.to_dict()["artifacts"] == previous_artifacts
    assert restored.blender_input_path == job.blender_input_path
    assert changes == [job.job_id]
    assert len(finished) == 1 and finished[0][:2] == (operation, False)
    assert not pipeline.busy
    assert pipeline._log_handle is None
    # A late terminal signal must not overwrite state or issue a second notice.
    process.finish()
    assert len(finished) == 1
    assert jobs.load(job.job_id).latest_blender_request.status == expected


@pytest.mark.parametrize("operation", ["blender_plan", "blender"])
def test_restart_recovers_blender_operation_without_losing_previous_results(
    blender_pipeline, operation
):
    pipeline, process, jobs, job = blender_pipeline
    start_operation(pipeline, process, job, operation)
    previous_artifacts = job.to_dict()["artifacts"]

    restored = JobService(jobs.root).recover_interrupted()[0]

    assert restored.latest_blender_request.status == "failed"
    assert restored.stages["blender"].status == (
        "completed" if operation == "blender_plan" else "failed"
    )
    assert restored.to_dict()["artifacts"] == previous_artifacts
    assert restored.blender_input_path == job.blender_input_path
    assert jobs.load(job.job_id).latest_blender_request.status == "failed"


@pytest.mark.parametrize("operation", ["blender_plan", "blender"])
def test_completed_operation_wins_over_late_cancel(blender_pipeline, operation):
    pipeline, process, jobs, job = blender_pipeline
    request = start_operation(pipeline, process, job, operation)
    if operation == "blender":
        write_execution(request)
    finished = []
    pipeline.operation_finished.connect(lambda *args: finished.append(args))
    process.finish()
    completed = jobs.load(job.job_id).to_dict()

    pipeline.cancel_active()
    process.finish()

    assert jobs.load(job.job_id).to_dict() == completed
    assert request.status == ("completed" if operation == "blender" else "awaiting_approval")
    assert len(finished) == 1 and finished[0][:2] == (operation, True)


def test_retry_after_cancel_completes_new_request(blender_pipeline):
    pipeline, process, jobs, job = blender_pipeline
    first = start_operation(pipeline, process, job, "blender")
    pipeline.cancel_active()
    second = start_operation(pipeline, process, job, "blender")
    write_execution(second)
    process.finish()

    restored = jobs.load(job.job_id)
    assert [request.status for request in restored.blender_requests] == ["cancelled", "completed"]
    assert first.version < second.version
    assert restored.stages["blender"].status == "completed"
    assert Path(restored.blender_input_path).name == "edited.blend"


def test_busy_blender_entrypoints_do_not_mutate_requests_or_stage(blender_pipeline):
    pipeline, process, jobs, job = blender_pipeline
    start_operation(pipeline, process, job, "blender_plan")
    before = jobs.load(job.job_id).to_dict()

    with pytest.raises(RuntimeError, match="다른 작업"):
        pipeline.start_proposal(job, "another request")
    with pytest.raises(RuntimeError, match="다른 작업"):
        pipeline.approve(job)

    assert jobs.load(job.job_id).to_dict() == before
    assert len(process.commands) == 1


def test_recovery_closes_old_orphans_and_preserves_approval_and_completed_stage(blender_pipeline):
    pipeline, process, jobs, job = blender_pipeline
    awaiting = start_operation(pipeline, process, job, "blender_plan")
    process.finish()
    statuses = ["planning", "running", "completed", "failed", "cancelled"]
    job.blender_requests[0:0] = [
        BlenderRequest("old request", "old.blend", f"old-v{version}", version, status=status)
        for version, status in enumerate(statuses, start=2)
    ]
    jobs.save(job)
    before_artifacts = job.to_dict()["artifacts"]

    restored = JobService(jobs.root).recover_interrupted()[0]

    assert [request.status for request in restored.blender_requests] == [
        "failed",
        "failed",
        "completed",
        "failed",
        "cancelled",
        "awaiting_approval",
    ]
    assert restored.latest_blender_request.plan_sha256 == awaiting.plan_sha256
    assert restored.latest_blender_request.plan == awaiting.plan
    assert restored.stages["blender"].status == "completed"
    assert restored.to_dict()["artifacts"] == before_artifacts
    once = restored.to_dict()
    assert JobService(jobs.root).recover_interrupted()[0].to_dict() == once


@pytest.mark.parametrize("operation", ["blender_plan", "blender"])
@pytest.mark.parametrize("failure", ["log_permission", "start_raise", "start_signal_and_raise"])
def test_synchronous_preparation_failure_terminates_once_and_can_retry(
    blender_pipeline, monkeypatch, operation, failure
):
    pipeline, process, jobs, job = blender_pipeline
    if operation == "blender":
        start_operation(pipeline, process, job, "blender_plan")
        process.finish()
    previous_artifacts = job.to_dict()["artifacts"]
    finished = []
    pipeline.operation_finished.connect(lambda *args: finished.append(args))

    def failed_log(_path):
        raise PermissionError("controlled log permission denied")

    def failed_start(_command):
        if failure == "start_signal_and_raise":
            process.fail_to_start()
        raise RuntimeError("controlled synchronous start failure")

    with monkeypatch.context() as patch:
        if failure == "log_permission":
            patch.setattr(pipeline, "_prepare_log", failed_log)
        else:
            patch.setattr(process, "start", failed_start)
        if operation == "blender_plan":
            pipeline.start_proposal(job, "BLEND로 저장")
        else:
            pipeline.approve(job)

    restored = jobs.load(job.job_id)
    assert restored.latest_blender_request.status == "failed"
    assert restored.stages["blender"].status == (
        "completed" if operation == "blender_plan" else "failed"
    )
    assert restored.to_dict()["artifacts"] == previous_artifacts
    assert len(finished) == 1 and finished[0][:2] == (operation, False)
    assert not pipeline.busy
    assert pipeline._active_blender_request is None
    assert pipeline._log_handle is None

    retried = start_operation(pipeline, process, job, "blender")
    write_execution(retried)
    process.finish()
    restored = jobs.load(job.job_id)
    assert [request.status for request in restored.blender_requests] == ["failed", "completed"]
    assert restored.stages["blender"].status == "completed"
