from forgeflow.ui.main_window import MainWindow


def test_environment_summary_counts_issues_and_keeps_details_collapsed(config):
    window = MainWindow(config, run_environment_checks=False)
    try:
        assert window.environment_details.isHidden()
        assert window.environment_summary.text() == "환경 확인 전"
        checks = {key: {"ok": True, "detail": "정상"} for key in window.environment_labels}
        window._environment_ready(checks)
        assert window.environment_summary.text() == "환경 정상"
        checks["gpu"] = {"ok": False, "detail": "GPU 연결 실패"}
        checks["unirig"] = {"ok": True, "warning": True, "detail": "commit 확인 필요"}
        window._environment_ready(checks)
        assert window.environment_summary.text() == "확인 필요 2건"
        assert "GPU 연결 실패" in window.environment_summary.toolTip()
        assert window.environment_details.isHidden()
        window.environment_toggle.click()
        assert not window.environment_details.isHidden()
        assert window.environment_toggle.text() == "상세 접기"
        assert config.unity_agent_model in window.environment_labels["ollama_unity"].text()
        window._environment_ready(checks)
        assert not window.environment_details.isHidden()
        window.environment_toggle.click()
        assert window.environment_details.isHidden()
        del checks["gpu"]
        window._environment_ready(checks)
        assert window.environment_summary.text() == "확인 필요 2건"
    finally:
        window.close()
