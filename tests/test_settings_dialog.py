from dataclasses import replace
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtNetwork import QNetworkReply

from forgeflow.ui.settings_dialog import SettingsDialog, validate_server_url

_APP = QApplication.instance() or QApplication([])
_APP.setQuitOnLastWindowClosed(False)


@pytest.fixture
def valid_config(config, tmp_path):
    config.blender_executable.write_bytes(b"test")
    return replace(config, unity_agent_root=config.blender_agent_root, unity_mcp_root=config.blender_mcp_root)


@pytest.mark.parametrize("address", ["", "localhost:11434", "ftp://localhost", "http://", "http://a:70000", "http://a:bad", "http://a b", "http://a?x=1"])
def test_invalid_server_address(address):
    with pytest.raises(ValueError):
        validate_server_url(address)


@pytest.mark.parametrize("address", ["http://127.0.0.1:11434", "https://example.com/ollama", "http://[::1]:11434"])
def test_valid_server_address(address):
    assert validate_server_url(address + "/") == address


@pytest.mark.parametrize("field,value", [("jobs_root", ""), ("modeling_root", "relative"), ("blender_executable", ""), ("ollama_base_url", "broken")])
def test_invalid_settings_stay_open(valid_config, field, value):
    dialog = SettingsDialog(valid_config, load_models=False)
    dialog.edits[field].setText(value)
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.error_label.text()
    dialog.close()


def test_new_jobs_directory_and_model_selection(valid_config, tmp_path):
    dialog = SettingsDialog(valid_config, load_models=False)
    target = tmp_path / "new" / "jobs"
    dialog.edits["jobs_root"].setText(str(target))
    combo = dialog.model_combos["unity_agent_model"]
    combo.addItems(["first:latest", "uncensored:q4"])
    combo.setCurrentIndex(1)
    updated = dialog.value(valid_config)
    assert updated.jobs_root == target
    assert updated.unity_agent_model == "uncensored:q4"
    assert not target.exists()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_browse_cancel_preserves_path_and_file_selection(valid_config, monkeypatch):
    dialog = SettingsDialog(valid_config, load_models=False)
    monkeypatch.setattr("forgeflow.ui.settings_dialog.QFileDialog.getExistingDirectory", lambda *args: "")
    dialog.browse_path("jobs_root")
    assert dialog.edits["jobs_root"].text() == str(valid_config.jobs_root)
    monkeypatch.setattr("forgeflow.ui.settings_dialog.QFileDialog.getOpenFileName", lambda *args: (str(valid_config.blender_executable), ""))
    dialog.browse_path("blender_executable")
    assert dialog.edits["blender_executable"].text() == str(valid_config.blender_executable)
    dialog.close()


class Reply:
    def __init__(self, error=False):
        self.failed = error
        self.deleted = False
    def error(self):
        return QNetworkReply.NetworkError.ConnectionRefusedError if self.failed else QNetworkReply.NetworkError.NoError
    def errorString(self):
        return "offline"
    def readAll(self):
        return json.dumps({"models": [{"name": "new:q4"}, {"name": "other:q8"}]}).encode()
    def deleteLater(self):
        self.deleted = True


def test_model_list_preserves_typed_selection_and_ignores_stale_reply(valid_config):
    dialog = SettingsDialog(valid_config, load_models=False)
    combo = dialog.model_combos["unity_agent_model"]
    combo.setCurrentText("typed:q4")
    reply = Reply()
    dialog._models_ready(reply, ["unity_agent_model"], "http://localhost", 0)
    assert combo.currentText() == "typed:q4"
    assert combo.count() == 2
    assert reply.deleted
    dialog._models_ready(Reply(True), ["unity_agent_model"], "http://localhost", 0)
    assert "조회 실패" in dialog.model_status["unity_agent_model"].text()
    dialog._generation = 1
    combo.clear()
    combo.setCurrentText("updated")
    dialog._models_ready(Reply(), ["unity_agent_model"], "http://old", 0)
    assert combo.count() == 0
    assert combo.currentText() == "updated"
    dialog.close()
