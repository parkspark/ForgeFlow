"""Cheap content checks before launching expensive external engines."""

from __future__ import annotations

import json
import struct
from pathlib import Path

from PySide6.QtGui import QImageReader


def validate_image_content(path: Path) -> tuple[int, int]:
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    detected = bytes(reader.format()).lower()
    if detected not in {b"png", b"jpeg", b"jpg"}:
        raise ValueError("PNG/JPEG 이미지 내용이 아닙니다.")
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > 64_000_000:
        raise ValueError("이미지를 읽을 수 없거나 6,400만 픽셀을 초과합니다.")
    decoded = reader.read()
    if decoded.isNull():
        raise ValueError("이미지 내용이 손상되었습니다: " + reader.errorString())
    return decoded.width(), decoded.height()


def preview_signature(path: Path) -> tuple[int, int] | None:
    if not path.is_file():
        return None
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def validate_glb(path: Path) -> dict:
    """Check GLB v2 framing, mesh accessors and embedded buffer availability."""
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            header = stream.read(12)
            if len(header) != 12:
                raise ValueError("GLB 헤더가 손상되었습니다.")
            magic, version, length = struct.unpack("<4sII", header)
            if magic != b"glTF" or version != 2 or length != size:
                raise ValueError("유효한 GLB 2.0 파일이 아닙니다.")
            document = None
            binary_length = 0
            while stream.tell() < size:
                chunk = stream.read(8)
                if len(chunk) != 8:
                    raise ValueError("GLB chunk 헤더가 손상되었습니다.")
                length, kind = struct.unpack("<II", chunk)
                if length % 4 or length > size - stream.tell():
                    raise ValueError("GLB chunk 범위가 올바르지 않습니다.")
                if document is None:
                    if kind != 0x4E4F534A or length > 64 * 1024 * 1024:
                        raise ValueError("GLB JSON chunk가 올바르지 않습니다.")
                    document = json.loads(stream.read(length).decode("utf-8"))
                elif kind == 0x004E4942:
                    if binary_length:
                        raise ValueError("GLB BIN chunk가 중복되었습니다.")
                    binary_length = length
                    stream.seek(length, 1)
                else:
                    stream.seek(length, 1)
        if not isinstance(document, dict) or document.get("asset", {}).get("version") != "2.0":
            raise ValueError("GLB asset 버전이 올바르지 않습니다.")
        accessors = document.get("accessors", [])
        primitives = [p for mesh in document.get("meshes", []) for p in mesh.get("primitives", [])]
        if not primitives:
            raise ValueError("리깅할 메시가 GLB에 없습니다.")
        for primitive in primitives:
            index = primitive.get("attributes", {}).get("POSITION")
            if type(index) is not int or not 0 <= index < len(accessors):
                raise ValueError("GLB POSITION accessor가 없습니다.")
            accessor = accessors[index]
            if accessor.get("type") != "VEC3" or accessor.get("count", 0) <= 0:
                raise ValueError("GLB 메시 정점이 올바르지 않습니다.")
        for buffer in document.get("buffers", []):
            if "uri" not in buffer and not 0 < buffer.get("byteLength", 0) <= binary_length:
                raise ValueError("GLB 내장 geometry buffer가 손상되었습니다.")
        return document
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        KeyError,
        AttributeError,
        IndexError,
        struct.error,
    ) as exc:
        raise ValueError(f"GLB 내용을 읽을 수 없습니다: {path.name}") from exc
