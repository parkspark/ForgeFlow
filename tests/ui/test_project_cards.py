from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel

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
