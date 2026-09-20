from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor

from forgeflow.domain.job import Job, UnitySession, UnityTurn
from forgeflow.ui.panels.unity_panel import UnityPanel
from forgeflow.ui.theme import build_stylesheet


def test_unity_panel_prioritizes_chat_and_scrolls_secondary_controls(qapp):
    panel = UnityPanel()
    panel.resize(1120, 760)
    panel.show()
    qapp.processEvents()

    assert panel.main_splitter.orientation() == Qt.Orientation.Horizontal
    assert panel.sidebar_scroll.widgetResizable() is True
    assert panel.sidebar_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert panel.main_splitter.sizes()[0] > panel.main_splitter.sizes()[1]
    assert panel.chat.minimumHeight() >= 240
    assert panel.screenshot_preview.isHidden()
    assert panel.send_button.text() == "전송  Ctrl+Enter"

    panel.sidebar_toggle.setChecked(True)
    assert panel.sidebar_scroll.isHidden()
    assert panel.sidebar_toggle.text() == "보조 패널 보기"
    panel.sidebar_toggle.setChecked(False)
    assert panel.sidebar_scroll.isVisible()

    panel.close()


def make_job() -> Job:
    return Job("job-unity", "UI 검토", "", "", "input.png")


def make_turn(status="succeeded", turn_id="turn-001") -> UnityTurn:
    return UnityTurn(turn_id, "session-001", "씬을 확인해줘", "씬을 확인해줘", status=status)


def ready_panel() -> UnityPanel:
    panel = UnityPanel()
    panel.set_job(make_job())
    panel.set_session(UnitySession("session-001", "C:/UnityProject", status="ready"))
    return panel


def test_disconnected_shortcut_keeps_draft_and_does_not_send(qapp):
    panel = UnityPanel()
    panel.set_job(make_job())
    sent = []
    panel.send_requested.connect(lambda *args: sent.append(args))
    panel.input.setPlainText("아직 연결되지 않은 요청")

    panel.send_shortcut.activated.emit()

    assert sent == []
    assert panel.input.toPlainText() == "아직 연결되지 않은 요청"
    assert not panel.send_button.isEnabled()
    assert not panel.cancel_button.isEnabled()
    assert "프로젝트를 연 뒤" in panel.request_status.text()
    panel.close()


def test_running_turn_blocks_duplicate_send_but_keeps_next_draft(qapp):
    panel = ready_panel()
    panel.job.unity_turns.append(make_turn("running"))
    panel.set_job(panel.job)
    panel.input.setPlainText("다음 요청")
    sent = []
    panel.send_requested.connect(lambda *args: sent.append(args))

    panel.send_keypad_shortcut.activated.emit()

    assert sent == []
    assert panel.input.toPlainText() == "다음 요청"
    assert not panel.send_button.isEnabled()
    assert panel.cancel_button.isEnabled()
    assert "실행 중" in panel.request_status.text()
    panel.close()


def test_unaccepted_or_failed_send_preserves_user_input(qapp):
    panel = ready_panel()
    panel.input.setPlainText("긴 요청 내용")
    panel._send()
    assert panel.input.toPlainText() == "긴 요청 내용"
    assert "보관했습니다" in panel.request_status.text()

    def fail_send(text, *_):
        turn = make_turn("failed")
        turn.user_text = text
        turn.error = "연결이 끊겼습니다."
        panel.job.unity_turns.append(turn)
        panel.set_job(panel.job)

    panel.send_requested.connect(fail_send)
    panel._send()
    assert panel.input.toPlainText() == "긴 요청 내용"
    assert "연결이 끊겼습니다" in panel.review_next_step.text()
    assert "[실패 원인]\n연결이 끊겼습니다" in panel.chat.toPlainText()
    panel.close()


def test_accepted_send_clears_only_submitted_draft_and_updates_actions(qapp):
    panel = ready_panel()
    assert not panel.send_button.isEnabled()
    assert not panel.cancel_button.isEnabled()

    def accept_send(text, *_):
        turn = make_turn("running")
        turn.user_text = text
        panel.job.unity_turns.append(turn)
        panel.set_job(panel.job)

    panel.send_requested.connect(accept_send)
    panel.input.setPlainText("이 요청을 실행해줘")
    assert panel.send_button.isEnabled()
    panel._send()
    assert panel.input.toPlainText() == ""
    assert not panel.send_button.isEnabled()
    assert panel.cancel_button.isEnabled()
    panel.close()


def test_pipeline_busy_blocks_send_and_repair(qapp):
    panel = ready_panel()
    panel.job.unity_turns.append(make_turn("failed"))
    panel.set_job(panel.job, busy=True)
    panel.input.setPlainText("요청")
    panel.review_note.setPlainText("카메라 위치 수정")
    assert not panel.send_button.isEnabled()
    assert not panel.repair_button.isEnabled()
    assert "다른 단계" in panel.request_status.text()
    panel.set_job(panel.job, busy=False)
    assert panel.send_button.isEnabled()
    assert panel.repair_button.isEnabled()
    panel.close()


def test_review_draft_survives_refresh_and_repair_requires_feedback(qapp):
    panel = ready_panel()
    panel.job.unity_turns.append(make_turn("failed"))
    panel.set_job(panel.job)
    assert not panel.repair_button.isEnabled()
    panel.review_note.setPlainText("전신이 보이도록 카메라를 뒤로 이동")
    panel.set_job(panel.job)
    assert panel.review_note.toPlainText() == "전신이 보이도록 카메라를 뒤로 이동"
    assert panel.repair_button.isEnabled()
    panel.job.unity_turns.append(make_turn("running", "turn-002"))
    panel.set_job(panel.job)
    assert panel.review_note.toPlainText() == ""
    assert not panel.repair_button.isEnabled()
    panel.close()


def test_history_refresh_and_stream_do_not_interrupt_reading(qapp):
    panel = ready_panel()
    turn = make_turn("running")
    turn.assistant_text = "\n".join(f"이전 메시지 {i}" for i in range(180))
    panel.job.unity_turns.append(turn)
    panel.set_job(panel.job)
    panel.resize(1000, 720)
    panel.show()
    qapp.processEvents()
    cursor = panel.chat.textCursor()
    cursor.setPosition(1)
    cursor.setPosition(6, QTextCursor.MoveMode.KeepAnchor)
    panel.chat.setTextCursor(cursor)
    panel.chat.verticalScrollBar().setValue(20)
    selected = panel.chat.textCursor().selectedText()
    position = panel.chat.verticalScrollBar().value()

    turn.assistant_text += "\n새 진행 상황"
    panel.set_job(panel.job)
    assert panel.chat.verticalScrollBar().value() == position
    assert panel.chat.textCursor().selectedText() == selected
    panel.append_event({"type": "assistant_text", "text": "\n추가 응답"})
    assert panel.chat.verticalScrollBar().value() == position
    assert panel.chat.textCursor().selectedText() == selected
    panel.close()


def test_review_summarizes_missing_checks_and_enables_only_available_files(qapp, tmp_path):
    panel = ready_panel()
    turn = make_turn()
    turn.automated_status = "verified"
    turn.requested_checks = ["scene_objects", "screenshot"]
    turn.measured_checks = ["scene_objects"]
    turn.unmapped_requirements = ["애니메이션 품질"]
    panel.job.unity_turns.append(turn)
    panel.set_job(panel.job)

    assert "측정 1개 / 요청 2개" in panel.checks.toPlainText()
    assert "측정하지 못한 항목\n• 스크린샷" in panel.checks.toPlainText()
    assert "직접 확인할 항목" in panel.automated_result.text()
    assert panel.accept_button.isEnabled()  # Human review remains an explicit choice.
    assert not panel.screenshot_button.isEnabled()
    assert not panel.receipt_button.isEnabled()
    assert not panel.log_button.isEnabled()
    assert not panel.scene_button.isEnabled()

    receipt = tmp_path / "receipt.json"
    receipt.write_text("{}", encoding="utf-8")
    turn.receipt_path = str(receipt)
    turn.run_log_path = str(tmp_path / "missing.log")
    panel.set_job(panel.job)
    assert panel.receipt_button.isEnabled()
    assert not panel.log_button.isEnabled()
    opened = []
    panel.open_path_requested.connect(opened.append)
    panel.receipt_button.click()
    assert opened == [str(receipt)]
    panel.close()


def test_connection_failure_is_visible_even_with_cached_identity(qapp):
    panel = ready_panel()
    assert panel.project_path.text() == "C:/UnityProject"
    assert not panel.project_path.isEnabled()
    assert not panel.browse_button.isEnabled()
    panel.set_session(UnitySession(
        "session-001", "C:/UnityProject", status="failed", error="Bridge 연결 시간 초과",
        project_identity={"productName": "Last project", "projectPath": "C:/UnityProject"},
    ))
    assert "Bridge 연결 시간 초과" in panel.identity_status.text()
    assert panel.identity_status.property("connectionState") == "error"
    assert panel.connect_button.text() == "다시 연결"
    assert panel.project_path.isEnabled()
    panel.close()


def test_job_switch_separates_prompt_and_project_drafts(qapp):
    panel = UnityPanel()
    first = make_job()
    first.unity_project_path = "C:/Unity/FirstSaved"
    second = Job("job-second", "두 번째 작업", "", "", "second.png")
    second.unity_project_path = "C:/Unity/SecondSaved"
    panel.set_job(first)
    panel.input.setPlainText("첫 번째 작업의 미전송 요청")
    panel.project_path.setText("C:/Unity/FirstDraft")

    panel.set_job(second)

    assert panel.input.toPlainText() == ""
    assert panel.project_path.text() == "C:/Unity/SecondSaved"
    panel.input.setPlainText("두 번째 작업의 요청")
    panel.project_path.setText("C:/Unity/SecondDraft")
    panel.set_job(first)
    assert panel.input.toPlainText() == "첫 번째 작업의 미전송 요청"
    assert panel.project_path.text() == "C:/Unity/FirstDraft"
    panel.set_job(second)
    assert panel.input.toPlainText() == "두 번째 작업의 요청"
    assert panel.project_path.text() == "C:/Unity/SecondDraft"
    panel.close()


def test_same_job_refresh_preserves_edited_or_cleared_project_and_prompt(qapp):
    panel = UnityPanel()
    job = make_job()
    job.unity_project_path = "C:/Unity/Saved"
    panel.set_job(job)
    panel.input.setPlainText("아직 작성 중인 요청")
    panel.project_path.setText("C:/Unity/Edited")

    panel.set_job(Job.from_dict(job.to_dict()))

    assert panel.input.toPlainText() == "아직 작성 중인 요청"
    assert panel.project_path.text() == "C:/Unity/Edited"
    panel.project_path.clear()
    panel.set_job(job)
    assert panel.project_path.text() == ""
    panel.close()


def test_job_without_registered_project_never_inherits_previous_path(qapp):
    panel = UnityPanel()
    first = make_job()
    first.unity_project_path = "C:/Unity/FirstSaved"
    panel.set_job(first)
    panel.input.setPlainText("첫 번째 작업 요청")

    panel.set_job(Job("job-new", "새 작업", "", "", "new.png"))

    assert panel.project_path.text() == ""
    assert panel.input.toPlainText() == ""
    assert not panel.connect_button.isEnabled()
    panel.close()


def test_unity_sidebar_does_not_expand_for_long_paths_set_before_show(qapp):
    panel = UnityPanel()
    panel.setStyleSheet(build_stylesheet("dark"))
    panel.resize(1120, 760)
    long_path = (
        r"C:\Users\park\Documents\ForgeFlow\jobs\job-20260827-160714-46d8a049"
        r"\rigging\v001\rig_job-20260827-160714-46d8a049_v001_humanoid.fbx"
    )
    panel.fbx_label.setText(long_path)
    panel.asset_label.setText(
        "Assets/ForgeFlow/job-20260827-160714-46d8a049/Models/v006/Character_humanoid.fbx"
    )
    panel.identity_status.setText(f"실제 프로젝트: ForgeFlow · {long_path}")

    panel.show()
    for _ in range(3):
        qapp.processEvents()

    sidebar_body = panel.sidebar_scroll.widget()
    assert sidebar_body.width() <= panel.sidebar_scroll.viewport().width()
    assert panel.sidebar_scroll.horizontalScrollBar().maximum() == 0

    panel.close()
