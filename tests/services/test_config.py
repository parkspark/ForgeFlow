from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from forgeflow import config as config_module
from forgeflow.config import AppConfig


def write_settings(path: Path, payload) -> bytes:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path.write_bytes(data)
    return data


def test_first_launch_uses_defaults_without_writing(config, tmp_path):
    path = tmp_path / "new-profile" / "config.json"

    loaded, warnings = AppConfig.load_with_diagnostics(path)

    assert loaded == AppConfig()
    assert warnings == []
    assert not path.parent.exists()


def test_valid_legacy_settings_and_unknown_fields_are_preserved(config, tmp_path):
    path = tmp_path / "config.json"
    jobs_root = tmp_path / "작업 폴더"
    original = write_settings(
        path,
        {
            "jobs_root": str(jobs_root),
            "unity_agent_model": "custom/model:latest",
            "theme": "light",
            "future_option": {"enabled": True},
        },
    )

    loaded, warnings = AppConfig.load_with_diagnostics(path)

    assert loaded.jobs_root == jobs_root
    assert loaded.unity_agent_model == "custom/model:latest"
    assert loaded.theme == "light"
    assert warnings == []
    assert AppConfig.load(path) == loaded
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "data",
    [
        b'{"broken":',
        b"null",
        b"[]",
        b'"text"',
        b"\xff",
        b"[" * 2000 + b"]" * 2000,
        b'{"future_option":' + b"9" * 5000 + b"}",
    ],
)
def test_corrupt_source_recovers_in_memory_and_keeps_original(config, tmp_path, data):
    path = tmp_path / "config.json"
    path.write_bytes(data)

    loaded, warnings = AppConfig.load_with_diagnostics(path)

    assert loaded == AppConfig()
    assert warnings
    assert any("기본값" in warning for warning in warnings)
    assert path.read_bytes() == data
    assert sorted(item.name for item in tmp_path.glob("config*")) == ["config.json"]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("jobs_root", None),
        ("jobs_root", "relative-jobs"),
        ("modeling_root", []),
        ("unity_agent_root", ""),
        ("blender_mcp_root", "invalid\x00path"),
        ("blender_executable", "blender.exe"),
        ("ollama_model", 42),
        ("unity_agent_model", "model with spaces"),
        ("ollama_base_url", "http://localhost:bad"),
        ("theme", ["dark"]),
        ("theme", "unsupported"),
    ],
)
def test_bad_field_recovers_without_losing_other_valid_fields(config, tmp_path, key, value):
    path = tmp_path / "config.json"
    original = write_settings(path, {key: value, "blender_agent_root": str(tmp_path / "custom")})

    loaded, warnings = AppConfig.load_with_diagnostics(path)

    assert getattr(loaded, key) == getattr(AppConfig(), key)
    assert loaded.blender_agent_root == tmp_path / "custom"
    assert any(key in warning for warning in warnings)
    assert path.read_bytes() == original


def test_corrupt_source_uses_last_valid_backup_without_writing(config, tmp_path):
    path = tmp_path / "config.json"
    path.write_bytes(b"broken json")
    backup = path.with_name(path.name + ".bak")
    original_backup = write_settings(
        backup, {"jobs_root": str(tmp_path / "old-jobs"), "ollama_model": "last-good:latest"}
    )

    loaded, warnings = AppConfig.load_with_diagnostics(path)

    assert loaded.jobs_root == tmp_path / "old-jobs"
    assert loaded.ollama_model == "last-good:latest"
    assert any(str(backup) in warning for warning in warnings)
    assert path.read_bytes() == b"broken json"
    assert backup.read_bytes() == original_backup


@pytest.mark.parametrize("invalid_path", [None, "relative-jobs"])
def test_bad_fields_use_backup_but_current_valid_fields_win(config, tmp_path, invalid_path):
    path = tmp_path / "config.json"
    write_settings(path, {"jobs_root": invalid_path, "ollama_model": "current:latest"})
    write_settings(
        path.with_name(path.name + ".bak"),
        {"jobs_root": str(tmp_path / "old-jobs"), "ollama_model": "old:latest"},
    )

    loaded, warnings = AppConfig.load_with_diagnostics(path)

    assert loaded.jobs_root == tmp_path / "old-jobs"
    assert loaded.ollama_model == "current:latest"
    assert any("백업" in warning for warning in warnings)


def test_absolute_path_to_missing_folder_remains_a_valid_setting(config, tmp_path):
    path = tmp_path / "config.json"
    missing_root = tmp_path / "not-created-yet"
    write_settings(path, {"jobs_root": str(missing_root)})

    loaded, warnings = AppConfig.load_with_diagnostics(path)

    assert loaded.jobs_root == missing_root
    assert warnings == []
    assert not missing_root.exists()


def test_damaged_backup_is_not_treated_as_last_valid_config(config, tmp_path):
    path = tmp_path / "config.json"
    path.write_bytes(b"broken json")
    backup = path.with_name(path.name + ".bak")
    write_settings(backup, {"jobs_root": None, "ollama_model": "untrusted:latest"})

    loaded, warnings = AppConfig.load_with_diagnostics(path)

    assert loaded == AppConfig()
    assert any("백업도" in warning for warning in warnings)
    assert path.read_bytes() == b"broken json"


def test_unreadable_source_uses_backup_without_overwriting_source(config, tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    write_settings(path, {"ollama_model": "current:latest"})
    backup = path.with_name(path.name + ".bak")
    write_settings(backup, {"ollama_model": "last-good:latest"})
    real_read_bytes = Path.read_bytes

    def deny_source(selected):
        if selected == path:
            raise PermissionError("source read denied")
        return real_read_bytes(selected)

    monkeypatch.setattr(Path, "read_bytes", deny_source)

    loaded, warnings = AppConfig.load_with_diagnostics(path)

    assert loaded.ollama_model == "last-good:latest"
    assert any("읽을 수 없습니다" in warning for warning in warnings)
    assert json.loads(real_read_bytes(path))["ollama_model"] == "current:latest"


def test_save_keeps_previous_valid_config_as_exact_backup(config, tmp_path):
    path = tmp_path / "config.json"
    original = write_settings(path, {"ollama_model": "old:latest", "future_option": True})

    config.save(path)

    assert AppConfig.load(path) == config
    assert path.with_name(path.name + ".bak").read_bytes() == original
    assert not list(tmp_path.glob("*.corrupt-*.bak"))
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize(
    "data",
    [b"broken json", b"[]", b'{"jobs_root":null}', b'{"ollama_model":' + b"9" * 5000 + b"}"],
)
def test_save_keeps_corrupt_source_and_does_not_replace_valid_backup(config, tmp_path, data):
    path = tmp_path / "config.json"
    path.write_bytes(data)
    backup = path.with_name(path.name + ".bak")
    original_backup = write_settings(backup, {"ollama_model": "last-good:latest"})

    config.save(path)

    assert AppConfig.load(path) == config
    assert backup.read_bytes() == original_backup
    corrupt_backups = list(tmp_path.glob("config.json.corrupt-*.bak"))
    assert len(corrupt_backups) == 1
    assert corrupt_backups[0].read_bytes() == data


def test_repeated_corrupt_saves_never_overwrite_corrupt_evidence(config, tmp_path):
    path = tmp_path / "config.json"
    for data in (b"first broken file", b"second broken file"):
        path.write_bytes(data)
        config.save(path)

    assert {item.read_bytes() for item in tmp_path.glob("config.json.corrupt-*.bak")} == {
        b"first broken file",
        b"second broken file",
    }


def test_save_flushes_backups_and_new_settings_before_replacement(config, tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    write_settings(path, {"theme": "light"})
    operations = []
    real_fsync = config_module.os.fsync
    real_replace = config_module.os.replace

    def synced(descriptor):
        operations.append("fsync")
        real_fsync(descriptor)

    def replaced(source, target):
        operations.append("replace")
        real_replace(source, target)

    monkeypatch.setattr(config_module.os, "fsync", synced)
    monkeypatch.setattr(config_module.os, "replace", replaced)

    config.save(path)

    assert operations == ["fsync", "replace", "fsync", "replace"]


def test_failed_primary_replacement_keeps_original_and_backup(config, tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    original = write_settings(path, {"theme": "light"})
    real_replace = config_module.os.replace

    def fail_primary(source, target):
        if Path(target) == path:
            raise PermissionError("primary replace denied")
        real_replace(source, target)

    monkeypatch.setattr(config_module.os, "replace", fail_primary)

    with pytest.raises(PermissionError, match="primary replace denied"):
        config.save(path)

    assert path.read_bytes() == original
    assert path.with_name(path.name + ".bak").read_bytes() == original
    assert not list(tmp_path.glob(".*.tmp"))


def test_failed_backup_aborts_save_and_keeps_original(config, tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    original = write_settings(path, {"theme": "light"})

    def fail_backup(source, target):
        raise PermissionError("backup replace denied")

    monkeypatch.setattr(config_module.os, "replace", fail_backup)

    with pytest.raises(PermissionError, match="backup replace denied"):
        config.save(path)

    assert path.read_bytes() == original
    assert not path.with_name(path.name + ".bak").exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_overlapping_saves_use_separate_temporary_files(config, tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    original = write_settings(path, {"theme": "light"})
    barrier = threading.Barrier(2)
    real_write = config_module._atomic_write
    real_replace = config_module.os.replace
    sources = []
    lock = threading.Lock()

    def delayed_write(target, data):
        if target == path:
            barrier.wait(timeout=5)
        real_write(target, data)

    def record_replace(source, target):
        with lock:
            sources.append(Path(source))
        real_replace(source, target)

    monkeypatch.setattr(config_module, "_atomic_write", delayed_write)
    monkeypatch.setattr(config_module.os, "replace", record_replace)
    variants = [replace(config, ollama_model=f"writer-{index}:latest") for index in range(2)]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(variant.save, path) for variant in variants]
        for future in futures:
            future.result(timeout=10)

    assert len(sources) == len(set(sources)) == 4
    assert AppConfig.load(path) in variants
    assert path.with_name(path.name + ".bak").read_bytes() == original
    assert not list(tmp_path.glob(".*.tmp"))
