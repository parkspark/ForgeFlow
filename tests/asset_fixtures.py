import json
import struct

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage


def glb_bytes(tag="fixture"):
    geometry = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    document = {
        "asset": {"version": "2.0", "extras": {"tag": str(tag)}},
        "buffers": [{"byteLength": len(geometry)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(geometry)}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "type": "VEC3", "count": 3}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    chunks = struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
    chunks += struct.pack("<II", len(geometry), 0x004E4942) + geometry
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


def png_bytes():
    pixels = QImage(8, 8, QImage.Format.Format_RGBA8888)
    pixels.fill(0xFF4A90E2)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert pixels.save(buffer, "PNG")
    return bytes(data)
