from __future__ import annotations

import json

import pytest

from forgeflow.adapters.unity.receipts import collect_changed_assets, parse_receipt


def _write_events(tmp_path, events):
    path = tmp_path / "run.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    return path


def _result(name, payload, arguments=None, *, status="ok"):
    return {
        "event": "tool_result",
        "name": name,
        "arguments": arguments or {},
        "result": json.dumps({"status": status, "result": payload}),
    }


def test_failed_tools_code_and_asset_references_are_not_changed_assets(tmp_path):
    path = _write_events(
        tmp_path,
        [
            _result(
                "unity_create_scene",
                {"path": "Assets/Rejected.unity", "error": "Assets/Allowed/. Repeat the call."},
                {"path": "Assets/Rejected.unity"},
                status="error",
            ),
            _result(
                "unity_write_script",
                {
                    "written": r"C:\Game\Assets\Scripts\PlaceCharacter.cs",
                    "next_step": 'Use Assets/Character.fbx"; then compile.',
                },
                {
                    "path": "Assets/Scripts/PlaceCharacter.cs",
                    "content": 'var fbx = "Assets/Character.fbx";\n',
                },
            ),
            _result("unity_open_scene", {"path": "Assets/Reference.unity"}),
            _result(
                "unity_instantiate_prefab",
                {
                    "path": "Character/Model",
                    "assetPath": "Assets/Character.fbx",
                    "scenePath": "Assets/Scenes/CharacterTest.unity",
                },
                {"asset_path": "Assets/Character.fbx"},
            ),
            _result(
                "unity_create_gameobjects",
                {"paths": ["Assets/FakeObject.unity"], "created": 1},
            ),
            _result("unity_save_scene", {"saved": False, "scene": "Assets/Unsaved.unity"}),
        ],
    )

    assert collect_changed_assets(path) == [
        "Assets/Scripts/PlaceCharacter.cs",
        "Assets/Scenes/CharacterTest.unity",
    ]


def test_only_documented_output_fields_are_collected(tmp_path):
    path = _write_events(
        tmp_path,
        [
            _result(
                "unity_create_scene",
                {"path": "Assets/Main.unity", "recoveryPath": "Assets/Recovery/Old.unity"},
            ),
            _result("unity_save_scene", {"saved": True, "scene": "Assets/Main.unity"}),
            _result("unity_create_material", {"assetPath": "Assets/Materials/Hero.mat"}),
            _result("unity_write_script", {"written": "/game/Assets/Scripts/Hero.cs"}),
            _result("unity_delete_script", {"deleted": "Assets/Scripts/Removed.cs"}),
            _result("unity_install_level_loader", {"written": "Assets/Scripts/LevelLoader.cs"}),
            _result("unity_write_level", {"written": "Assets/StreamingAssets/Levels/Map.json"}),
        ],
    )
    assert collect_changed_assets(path) == [
        "Assets/Main.unity",
        "Assets/Recovery/Old.unity",
        "Assets/Materials/Hero.mat",
        "Assets/Scripts/Hero.cs",
        "Assets/Scripts/Removed.cs",
        "Assets/Scripts/LevelLoader.cs",
        "Assets/StreamingAssets/Levels/Map.json",
    ]


@pytest.mark.parametrize(
    "value",
    [
        'Assets/Scripts/Foo.cs";',
        "Assets/../Scripts/Foo.cs",
        "Use Assets/Scripts/Foo.cs",
        "Assets/Scripts/Foo.cs\nAssets/Other.cs",
        "Assets/Scripts/Foo.cs. Repeat the call.",
    ],
)
def test_malformed_output_path_is_not_registered(tmp_path, value):
    path = _write_events(tmp_path, [_result("unity_write_script", {"written": value})])
    assert collect_changed_assets(path) == []


def test_non_object_events_and_unstructured_results_are_ignored(tmp_path):
    path = _write_events(
        tmp_path,
        [
            [],
            None,
            {"event": "tool_result", "name": "unity_write_script", "result": "Assets/Foo.cs"},
            {"event": "tool_result", "name": "unity_write_script", "result": "[]"},
        ],
    )
    assert collect_changed_assets(path) == []


@pytest.mark.parametrize("payload", [[], None, "invalid receipt"])
def test_non_object_receipt_is_unavailable(tmp_path, payload):
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert parse_receipt(path) == {"automated_status": "unavailable"}


def test_camera_backup_manifest_records_only_successful_written_files(tmp_path):
    path = _write_events(tmp_path, [
        _result("unity_frame_character", {
            "backupPath": "Assets/ForgeFlow/job/Backups/BeforeCamera.unity",
            "settingsBackupPath": "Assets/ForgeFlow/job/Backups/BeforeCamera.json",
            "scenePath": "Assets/ForgeFlow/job/Scenes/UnsavedCamera.unity",
        }),
        _result("unity_frame_character", {
            "backupPath": "Assets/ForgeFlow/job/Backups/Failed.unity",
        }, status="error"),
    ])
    assert collect_changed_assets(path) == [
        "Assets/ForgeFlow/job/Backups/BeforeCamera.unity",
        "Assets/ForgeFlow/job/Backups/BeforeCamera.json",
    ]
