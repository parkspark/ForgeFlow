"""Interpret Unity verification receipts and mutation audit records."""

from __future__ import annotations

import json
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
    """Extract project-relative assets from mutation tool audit records."""
    if not jsonl_path:
        return []
    path = Path(jsonl_path)
    mutations = {
        "unity_create_gameobject",
        "unity_create_gameobjects",
        "unity_modify_gameobject",
        "unity_delete_gameobject",
        "unity_add_component",
        "unity_remove_component",
        "unity_set_component_property",
        "unity_create_material",
        "unity_create_scene",
        "unity_open_scene",
        "unity_save_scene",
        "unity_write_script",
        "unity_delete_script",
        "unity_write_level",
    }
    found: list[str] = []
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, str):
            normalized = node.replace("\\", "/")
            start = normalized.find("Assets/")
            if start >= 0:
                candidate = normalized[start:].split("\n", 1)[0].strip(" \"'")
                if candidate and candidate not in seen:
                    seen.add(candidate)
                    found.append(candidate)
        elif isinstance(node, dict):
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("event") != "tool_result" or event.get("name") not in mutations:
                    continue
                walk(event.get("arguments"))
                result = event.get("result")
                if isinstance(result, str):
                    try:
                        walk(json.loads(result))
                    except json.JSONDecodeError:
                        walk(result)
                else:
                    walk(result)
    except OSError:
        return []
    return found
