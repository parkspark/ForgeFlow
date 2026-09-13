from forgeflow.domain.job import STATUSES
from forgeflow.ui.status import STATUS_LABELS, status_text
from forgeflow.ui.main_window import MainWindow
from PySide6.QtWidgets import QApplication, QLabel
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
_APP = QApplication.instance() or QApplication([])
_APP.setQuitOnLastWindowClosed(False)


def test_all_stage_statuses_have_korean_labels():
    assert STATUSES <= STATUS_LABELS.keys()
    assert status_text('unexpected') == '확인 필요'
    assert status_text('awaiting_review') != status_text('completed')


def test_badges_update_without_changing_saved_states(config, image):
    window = MainWindow(config, run_environment_checks=False)
    try:
        job = window.jobs.create('상태 테스트', image)
        window.current_job = job
        job.stages['modeling'].status = 'running'
        window._job_changed(job)
        assert window.modeling_panel.status.text() == '모델링 · 진행 중'
        assert window.modeling_panel.status.property('statusTone') == 'active'
        job.stages['modeling'].status = 'failed'
        job.stages['modeling'].error = '입력 확인 필요'
        window._job_changed(job)
        assert window.modeling_panel.status.property('statusTone') == 'error'
        assert window.modeling_panel.error.text() == '입력 확인 필요'
        item = window.project_panel.jobs.item(0)
        card = window.project_panel.jobs.itemWidget(item)
        assert any(label.text() == '모델링 · 실패' for label in card.findChildren(QLabel))
        assert 'failed' not in item.text()
        assert job.stages['modeling'].status == 'failed'
    finally:
        window.close()
