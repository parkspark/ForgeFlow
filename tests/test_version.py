from __future__ import annotations

import re
import tomllib
from pathlib import Path

from forgeflow import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_version_is_current_release():
    assert __version__ == "0.2.0"


def test_project_metadata_uses_runtime_version_as_single_source():
    metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["dynamic"] == ["version"]
    assert metadata["tool"]["hatch"]["version"]["path"] == "src/forgeflow/__init__.py"


def test_windows_executable_version_matches_runtime_version():
    version_info = (PROJECT_ROOT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
    file_version = re.search(r"StringStruct\('FileVersion', '([^']+)'\)", version_info)
    product_version = re.search(r"StringStruct\('ProductVersion', '([^']+)'\)", version_info)
    assert file_version and file_version.group(1) == __version__
    assert product_version and product_version.group(1) == __version__
