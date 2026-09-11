import hashlib
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

from importlib.util import module_from_spec, spec_from_file_location

ROOT = Path(__file__).parents[1]
WORKER = ROOT / "src" / "SpotAnalysis.Worker" / "worker.py"
spec = spec_from_file_location("png_analysis", ROOT / "src" / "SpotAnalysis.Worker" / "png_analysis.py")
png = module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(png)


def write_png(path: Path, width: int, height: int, depth: int, color: int, rows: list[list[int]]) -> None:
    channels = 1 if color == 0 else 3
    raw_rows = []
    for row in rows:
        encoded = b""
        for value in row:
            values = value if channels == 3 and isinstance(value, tuple) else (value,) * channels
            encoded += b"".join(component.to_bytes(depth // 8, "big") for component in values)
        raw_rows.append(b"\0" + encoded)
    raw = b"".join(raw_rows)
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def run(request: dict) -> list[dict]:
    completed = subprocess.run([sys.executable, str(WORKER)], input=json.dumps(request) + "\n", text=True, capture_output=True, check=True)
    assert completed.stderr == ""
    return [json.loads(line) for line in completed.stdout.splitlines()]


def test_8_and_16_bit_png_and_worker_record(tmp_path: Path) -> None:
    for depth, value in ((8, 7), (16, 1025)):
        path = tmp_path / f"gray-{depth}.png"
        write_png(path, 2, 1, depth, 0, [[value, value + 1]])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        image = png.read_png(path, expected_sha256=digest, intensity_semantics_confirmed=True, uri_hint="fixture://gray")
        assert image["bit_depth"] == depth
        messages = run({"protocol_version": 1, "request_id": f"png-{depth}", "command": "analyze", "input": {"kind": "png", "path": str(path), "sha256": digest, "intensity_semantics_confirmed": True, "uri_hint": "fixture://gray"}})
        result = messages[-1]["result"]
        assert messages[-1]["status"] == "success"
        assert result["record_id"] and result["analysis_fingerprint"]
        assert result["input"]["sha256"] == digest
        assert result["result"]["pixel_count"] == 2


def test_png_input_failures_are_structured(tmp_path: Path) -> None:
    path = tmp_path / "gray.png"
    write_png(path, 1, 1, 8, 0, [[9]])
    base = {"kind": "png", "path": str(path), "intensity_semantics_confirmed": False}
    messages = run({"protocol_version": 1, "request_id": "unconfirmed", "command": "analyze", "input": base})
    assert messages[-1]["error"]["code"] == "intensity_semantics_unconfirmed"
    base["intensity_semantics_confirmed"] = True
    base["sha256"] = "0" * 64
    messages = run({"protocol_version": 1, "request_id": "mismatch", "command": "analyze", "input": base})
    assert messages[-1]["error"]["code"] == "hash_mismatch"
    rgb = tmp_path / "rgb.png"
    write_png(rgb, 1, 1, 8, 2, [[(1, 2, 1)]])
    messages = run({"protocol_version": 1, "request_id": "rgb", "command": "analyze", "input": {"kind": "png", "path": str(rgb), "intensity_semantics_confirmed": True}})
    assert messages[-1]["error"]["code"] == "rgb_channels_differ"
