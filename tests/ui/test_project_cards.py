from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from forgeflow.domain.job import Job
from forgeflow.services.job_service import JobService
from forgeflow.ui.panels.project_panel import JobName, ProjectPanel, modified_text


def test_job_card_shows_current_stage_thumbnail_and_hides_id(config, tmp_path):
    path = tmp_path / "input.png"
    image = QImage(40, 80, QImage.Format.Format_RGB32)
    image.fill(0xFF607080)
    image.save(str(path))
    job = JobService(config.jobs_root).create("긴 작업 이름 테스트", path)
    job.current_stage = "unity"
    job.stages["unity"].status = "awaiting_review"
    panel = ProjectPanel()
    try:
        panel.set_jobs([job])
        item = panel.jobs.item(0)
        card = panel.jobs.itemWidget(item)
        labels = card.findChildren(QLabel)
        assert any(label.text() == "Unity · 검토 대기" for label in labels)
        assert not any(label.text() == job.job_id for label in labels)
        assert job.job_id in item.toolTip()
        assert "최근 수정:" in item.toolTip()
        assert card.findChild(JobName).full_text == job.name
        assert not card.findChild(QLabel, "jobThumbnail").pixmap().isNull()
        assert panel.details_button.isEnabled()
        job.input_image_path = str(tmp_path / "missing.png")
        panel.upsert_job(job, 0, job.job_id)
        card = panel.jobs.itemWidget(panel.jobs.item(0))
        assert card.findChild(QLabel, "jobThumbnail").text() == "이미지\n없음"
        assert panel.jobs.currentItem().data(256) == job.job_id
        panel.set_jobs([])
        assert not panel.details_button.isEnabled()
    finally:
        panel.close()


def test_invalid_modified_time_has_readable_fallback():
    assert modified_text("invalid") == "시간 정보 없음"


def sample_job(number, name, stage="modeling", status="pending"):
    job = Job(
        job_id=f"job-{number:03}",
        name=name,
        created_at="2026-09-20T01:00:00+09:00",
        updated_at="2026-09-20T01:00:00+09:00",
        input_image_path="missing-test-image.png",
        current_stage=stage,
    )
    job.stages[stage].status = status
    return job


def visible_ids(panel):
    return [
        panel.jobs.item(row).data(Qt.ItemDataRole.UserRole)
        for row in range(panel.jobs.count())
        if not panel.jobs.item(row).isHidden()
    ]


def test_search_finds_name_id_current_stage_and_status_without_switching_jobs(qapp):
    panel = ProjectPanel()
    jobs = [
        sample_job(1, "Robot Hero", "modeling", "completed"),
        sample_job(2, "Forest Hero", "rigging", "failed"),
        sample_job(3, "Temple", "unity", "running"),
    ]
    selected = []
    panel.job_selected.connect(selected.append)
    try:
        panel.set_jobs(jobs, jobs[0].job_id)
        panel.search.setText("HERO 실패")
        assert visible_ids(panel) == [jobs[1].job_id]
        assert "1 / 전체 3" in panel.result_count.text()
        assert panel.jobs.currentItem() is None
        assert not panel.selection_notice.isHidden()
        assert not panel.details_button.isEnabled()
        assert selected == []
        panel.search.clear()
        assert panel.jobs.currentItem().data(Qt.ItemDataRole.UserRole) == jobs[0].job_id
        assert selected == []
        panel.search.setText("리깅")
        assert visible_ids(panel) == [jobs[1].job_id]
        panel.search.setText("JOB-003")
        assert visible_ids(panel) == [jobs[2].job_id]
        QTest.keyClick(panel.search, Qt.Key.Key_Return)
        assert selected == [jobs[2].job_id]
        assert panel.details_button.isEnabled()
        assert panel.selection_notice.isHidden()
    finally:
        panel.close()


def test_empty_and_no_match_states_offer_a_recovery_action():
    panel = ProjectPanel()
    try:
        assert "아직 작업이 없습니다" in panel.empty_state.text()
        assert panel.list_stack.currentWidget() is panel.empty_page
        assert panel.clear_search.isHidden()
        panel.set_jobs([sample_job(1, "Robot")])
        panel.search.setText("missing")
        assert panel.list_stack.currentWidget() is panel.empty_page
        assert "검색 결과가 없습니다" in panel.empty_state.text()
        assert not panel.clear_search.isHidden()
        panel.clear_search.click()
        assert panel.search.text() == ""
        assert panel.list_stack.currentWidget() is panel.jobs
        assert panel.jobs.currentItem().data(Qt.ItemDataRole.UserRole) == "job-001"
    finally:
        panel.close()


def test_refresh_preserves_selection_query_and_scroll_and_updates_search_metadata(qapp):
    panel = ProjectPanel()
    jobs = [sample_job(number, f"작업 {number}") for number in range(40)]
    selected = []
    panel.job_selected.connect(selected.append)
    try:
        panel.resize(320, 650)
        panel.show()
        panel.set_jobs(jobs, jobs[20].job_id)
        qapp.processEvents()
        panel.jobs.verticalScrollBar().setValue(15)
        scroll = panel.jobs.verticalScrollBar().value()
        panel.set_jobs(jobs)
        qapp.processEvents()
        assert panel.jobs.currentItem().data(Qt.ItemDataRole.UserRole) == jobs[20].job_id
        assert panel.jobs.verticalScrollBar().value() == scroll
        panel.upsert_job(jobs[0], 0)
        qapp.processEvents()
        assert panel.jobs.currentItem().data(Qt.ItemDataRole.UserRole) == jobs[20].job_id
        assert panel.jobs.verticalScrollBar().value() == scroll
        panel.search.setText("리깅 실패")
        assert visible_ids(panel) == []
        jobs[0].current_stage = "rigging"
        jobs[0].stages["rigging"].status = "failed"
        panel.upsert_job(jobs[0], 0)
        assert visible_ids(panel) == [jobs[0].job_id]
        assert panel.jobs.currentItem() is None
        panel.set_jobs(jobs)
        assert panel.search.text() == "리깅 실패"
        assert visible_ids(panel) == [jobs[0].job_id]
        panel.search.clear()
        assert panel.jobs.currentItem().data(Qt.ItemDataRole.UserRole) == jobs[20].job_id
        assert selected == []
    finally:
        panel.close()
