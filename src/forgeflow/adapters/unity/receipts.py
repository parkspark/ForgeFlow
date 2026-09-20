"""Interpret Unity verification receipts and mutation audit records."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _receipt_lists(payload: dict[str, Any], name: str) -> list[Any]:
    value = payload.get(name, [])
    return value if isinstance(value, list) else []


def parse_receipt(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {"automated_status": "unavailable"}
    receipt = Path(path)
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"automated_status": "unavailable"}
    if not isinstance(payload, dict):
        return {"automated_status": "unavailable"}
    requested = _receipt_lists(payload, "requested_checks")
    measured = _receipt_lists(payload, "measured_checks")
    skipped = _receipt_lists(payload, "skipped_checks")
    unmapped = _receipt_lists(payload, "unmapped_requirements")
    status = str(payload.get("status") or "").lower()
    if status == "failed":
        automated = "failed"
    elif not requested and not measured:
        automated = "unavailable"
    elif not requested or not measured or skipped or unmapped:
        automated = "partial"
    elif status == "verified":
        automated = "verified"
    else:
        automated = "partial"
    return {
        "automated_status": automated,
        "requested_checks": requested,
        "measured_checks": measured,
        "skipped_checks": skipped,
        "unmapped_requirements": unmapped,
        "receipt": payload,
    }


def collect_changed_assets(jsonl_path: str | Path | None) -> list[str]:
    """Collect explicit file outputs from successful mutation tool results.

    Arguments can contain source assets, C# code and rejected destinations.
    Only the tool's structured output fields identify files it actually changed.
    Scene-object mutations are represented when the scene is saved, not by
    GameObject hierarchy paths that happen to start with ``Assets/``.
    """
    if not jsonl_path:
        return []
    path = Path(jsonl_path)
    output_fields = {
        "unity_create_material": {"assetPath": ".mat"},
        "unity_create_scene": {"path": ".unity", "recoveryPath": ".unity"},
        "unity_instantiate_prefab": {"scenePath": ".unity"},
        "unity_save_scene": {"scene": ".unity"},
        "unity_write_script": {"written": ".cs"},
        "unity_delete_script": {"deleted": ".cs"},
        "unity_install_level_loader": {"written": ".cs"},
        "unity_write_level": {"written": ".json"},
    }
    found: list[str] = []
    seen: set[str] = set()

    def add_path(value: Any, suffix: str) -> None:
        if not isinstance(value, str):
            return
        normalized = value.replace("\\", "/")
        if normalized.startswith("Assets/"):
            candidate = normalized
        elif normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
            marker = normalized.find("/Assets/")
            if marker < 0:
                return
            candidate = normalized[marker + 1 :]
        else:
            return
        if (
            any(char in candidate for char in '\r\n\t"<>|?*:')
            or any(part in {"", ".", ".."} for part in candidate.split("/"))
            or not candidate.lower().endswith(suffix)
        ):
            return
        key = candidate.casefold()
        if key not in seen:
            seen.add(key)
            found.append(candidate)

    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict) or event.get("event") != "tool_result":
                    continue
                fields = output_fields.get(event.get("name"))
                if fields is None:
                    continue
                result = event.get("result")
                if isinstance(result, str):
                    try:
                        result = json.loads(result)
                    except json.JSONDecodeError:
                        continue
                if not isinstance(result, dict) or result.get("status") != "ok":
                    continue
                payload = result.get("result")
                if not isinstance(payload, dict):
                    continue
                if event.get("name") == "unity_save_scene" and payload.get("saved") is not True:
                    continue
                for field, suffix in fields.items():
                    add_path(payload.get(field), suffix)
    except OSError:
        return []
    return found
