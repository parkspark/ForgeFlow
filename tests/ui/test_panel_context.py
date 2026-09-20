"""Job/input isolation without launching Blender, UniRig, or other engines."""

from types import SimpleNamespace

from forgeflow.domain.job import BlenderRequest, Job, RiggingRequest
from forgeflow.ui.panels.blender_panel import BlenderPanel
from forgeflow.ui.panels.rigging_panel import RiggingPanel


def job(name, input_path=None):
    return Job(
        job_id=name, name=name, created_at="2026-09-20", updated_at="2026-09-20",
        input_image_path=f"{name}.png", blender_input_path=input_path or f"C:/fixtures/{name}.glb",
    )


def inspection(name):
    return {"data": {"objects": [{"name": name, "type": "MESH"}]}}


def rig_request(owner, version=1, input_path=None):
    request = RiggingRequest(
        input_path=input_path or owner.blender_input_path, input_sha256="fixture",
        output_directory=f"C:/fixtures/{owner.job_id}/v{version:03d}",
        version=version, seed=12345, safe_name=owner.job_id,
    )
    owner.rigging_requests.append(request)
    owner.stages["rigging"].status = "running"
    return request


def inputs(*paths):
    return [SimpleNamespace(path=path, label=path) for path in paths]


def test_blender_refresh_keeps_draft_and_cursor():
    panel = BlenderPanel()
    owner = job("A")
    panel.set_job(owner)
    panel.request.setPlainText("작성 중인 편집 요청")
    cursor = panel.request.textCursor()
    cursor.setPosition(3)
    panel.request.setTextCursor(cursor)
    panel.set_inspection(inspection("A_Mesh"))
    panel.set_job(owner, busy=True)
    assert panel.request.toPlainText() == "작성 중인 편집 요청"
    assert panel.request.textCursor().position() == 3
    assert "A_Mesh" in panel.scene.toPlainText()


def test_blender_switch_separates_and_restores_each_job_draft_inspection_plan():
    panel = BlenderPanel()
    a, b = job("A"), job("B")
    a.blender_requests.append(BlenderRequest(
        request="A만 편집", input_path=a.blender_input_path, output_directory="C:/fixtures/A/v001",
        version=1, status="awaiting_approval", plan={"steps": [
            {"number": 1, "description": "A 계획", "tool": "fixture", "arguments": {}},
        ]},
    ))
    panel.set_job(a)
    panel.request.setPlainText("A 초안")
    panel.set_inspection(inspection("A_Mesh"))
    assert panel.approve.isEnabled()
    panel.set_job(b)
    assert panel.request.toPlainText() == ""
    assert panel.scene.toPlainText() == ""
    assert panel.plan.toPlainText() == ""
    assert not panel.approve.isEnabled()
    panel.request.setPlainText("B 초안")
    panel.set_inspection(inspection("B_Mesh"))
    panel.set_job(a)
    assert panel.request.toPlainText() == "A 초안"
    assert "A_Mesh" in panel.scene.toPlainText()
    assert "B_Mesh" not in panel.scene.toPlainText()
    assert "A 계획" in panel.plan.toPlainText()
    assert panel.approve.isEnabled()
    panel.set_job(b)
    assert panel.request.toPlainText() == "B 초안"


def test_blender_input_version_change_has_its_own_draft_and_inspection():
    panel = BlenderPanel()
    owner = job("A", "C:/fixtures/A/v001.glb")
    panel.set_job(owner)
    panel.request.setPlainText("v1 초안")
    panel.set_inspection(inspection("v1_Mesh"))
    owner.blender_input_path = "C:/fixtures/A/v002.glb"
    panel.set_job(owner)
    assert not panel.request.toPlainText()
    assert not panel.scene.toPlainText()
    owner.blender_input_path = "C:/fixtures/A/v001.glb"
    panel.set_job(owner)
    assert panel.request.toPlainText() == "v1 초안"
    assert "v1_Mesh" in panel.scene.toPlainText()


def test_blender_late_inspection_is_routed_to_original_job():
    panel = BlenderPanel()
    a, b = job("A"), job("B")
    panel.set_job(a)
    panel.set_job(b)
    panel.set_inspection(inspection("A_Late"), job_id=a.job_id, input_path=a.blender_input_path)
    assert not panel.scene.toPlainText()
    panel.set_job(a)
    assert "A_Late" in panel.scene.toPlainText()


def test_blender_plan_for_old_input_cannot_be_approved():
    panel = BlenderPanel()
    owner = job("A")
    owner.blender_requests.append(BlenderRequest(
        request="이전 입력 계획", input_path="C:/fixtures/A/old.glb",
        output_directory="C:/fixtures/A/v001", version=1,
        status="awaiting_approval", plan={"steps": []},
    ))
    panel.set_job(owner)
    assert not panel.approve.isEnabled()
    assert panel.deny.isEnabled()
    assert "입력이 변경" in panel.plan.toPlainText()


def test_rigging_switch_resets_phase_and_log_then_restores_original_run():
    panel = RiggingPanel()
    a, b = job("A"), job("B")
    rig_request(a)
    panel.set_job(a, [])
    panel.append_log("A_skeleton.fbx", job=a)
    panel.set_job(b, [])
    assert panel.phase_status.text() == "세부 단계: 대기"
    assert not panel.log.toPlainText()
    panel.set_job(a, [])
    assert "1/5" in panel.phase_status.text()
    assert panel.log.toPlainText() == "A_skeleton.fbx"


def test_rigging_background_run_logs_do_not_change_selected_job():
    panel = RiggingPanel()
    a, b = job("A"), job("B")
    rig_request(a)
    panel.set_job(a, [])
    panel.set_job(b, [])
    panel.append_log("A_skin.fbx", job=a)
    assert not panel.log.toPlainText()
    assert panel.phase_status.text() == "세부 단계: 대기"
    panel.set_job(a, [])
    assert "2/5" in panel.phase_status.text()
    assert panel.log.toPlainText() == "A_skin.fbx"


def test_rigging_new_run_on_same_input_does_not_reuse_old_progress():
    panel = RiggingPanel()
    owner = job("A")
    rig_request(owner, 1)
    panel.set_job(owner, [])
    panel.append_log("preview saved", job=owner)
    panel.set_job(owner, [], busy=True)
    assert "5/5" in panel.phase_status.text()
    rig_request(owner, 2)
    panel.set_job(owner, [], busy=True)
    assert not panel.log.toPlainText()
    assert panel.phase_status.text() == "세부 단계: 환경/VRAM 준비"


def test_rigging_completed_to_pending_job_never_keeps_completed_phase():
    panel = RiggingPanel()
    a, b = job("A"), job("B")
    a.stages["rigging"].status = "completed"
    panel.set_job(a, [])
    assert panel.phase_status.text() == "세부 단계: 완료"
    panel.set_job(b, [])
    assert panel.phase_status.text() == "세부 단계: 대기"


def test_rigging_confirmation_is_bound_to_job_even_if_input_path_matches():
    panel = RiggingPanel()
    a, b = job("A", "C:/fixtures/shared.glb"), job("B", "C:/fixtures/shared.glb")
    candidates = inputs(a.blender_input_path)
    panel.set_job(a, candidates)
    panel.confirm_humanoid.setChecked(True)
    panel.set_job(a, candidates)
    assert panel.confirm_humanoid.isChecked()
    panel.set_job(b, candidates)
    assert not panel.confirm_humanoid.isChecked()
    assert not panel.run_button.isEnabled()


def test_rigging_preserves_each_jobs_selected_input_seed_and_busy_guard():
    panel = RiggingPanel()
    a, b = job("A"), job("B")
    candidates = inputs("C:/fixtures/v1.glb", "C:/fixtures/v2.glb")
    panel.set_job(a, candidates)
    panel.inputs.setCurrentIndex(1)
    panel.seed.setValue(77)
    panel.set_job(b, candidates)
    assert panel.inputs.currentIndex() == 0
    assert panel.seed.value() == 12345
    panel.set_job(a, candidates, busy=True)
    assert panel.inputs.currentIndex() == 1
    assert panel.seed.value() == 77
    panel.confirm_humanoid.setChecked(True)
    assert not panel.run_button.isEnabled()
