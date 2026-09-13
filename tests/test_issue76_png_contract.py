from __future__ import annotations

import hashlib
import os
from pathlib import Path
import struct
import time
import zlib
from dataclasses import asdict
from importlib.util import module_from_spec, spec_from_file_location

import numpy as np

from spot_analyzer import AnalysisConfiguration, AnalysisRegion
from spot_analyzer.input import decode_png
from spot_analyzer.worker import handle_request

ROOT = Path(__file__).parents[1]
_png_spec = spec_from_file_location("issue76_png_analysis", ROOT / "src" / "SpotAnalysis.Worker" / "png_analysis.py")
png_analysis = module_from_spec(_png_spec)
assert _png_spec.loader is not None
_png_spec.loader.exec_module(png_analysis)


def make_png(width: int, height: int, bit_depth: int, color_type: int, rows: list[list[int | tuple[int, ...]]], *, ancillary: bool = False) -> bytes:
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    sample_bytes = bit_depth // 8
    raw = bytearray()
    for row in rows:
        raw.append(0)
        for value in row:
            values = value if isinstance(value, tuple) else (value,) * channels
            for component in values:
                raw.extend(int(component).to_bytes(sample_bytes, "big"))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0)
    extras = chunk(b"gAMA", struct.pack(">I", 45455)) + chunk(b"sRGB", b"\x00") if ancillary else b""
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + extras + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")


def test_python_and_legacy_worker_decode_the_same_samples_and_identity(tmp_path: Path) -> None:
    values = [[0, 1, 255], [256, 32768, 65535]]
    payload = make_png(3, 2, 16, 0, values, ancillary=True)
    path = tmp_path / "中文 folder" / "input with spaces.PNG"
    path.parent.mkdir()
    path.write_bytes(payload)
    before = (path.stat().st_size, path.stat().st_mtime_ns, hashlib.sha256(payload).hexdigest())

    decoded = decode_png(path, confirm_relative_intensity=True)
    legacy = png_analysis.read_png(path, intensity_semantics_confirmed=True)

    assert decoded.image is not None
    assert np.array_equal(decoded.image.data, np.asarray(values, dtype=np.float64))
    assert legacy["samples"] == [item for row in values for item in row]
    assert legacy["width"] == decoded.image.data.shape[1]
    assert legacy["height"] == decoded.image.data.shape[0]
    assert legacy["bit_depth"] == decoded.image.bit_depth == 16
    assert decoded.image.dtype == legacy["dtype"] == "uint16"
    assert legacy["channels"] == decoded.image.channels == 1
    assert legacy["channels_identical"] is False
    assert legacy["sha256"] == decoded.image.sha256 == before[2]
    assert legacy["byte_order"] == decoded.image.byte_order == "big"
    assert decoded.image.metadata["gamma"] == 0.45455
    assert decoded.image.metadata["srgb"] == 0
    assert (path.stat().st_size, path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) == before


def test_equal_channel_rgb_is_one_8_bit_intensity_plane_and_preserves_fact(tmp_path: Path) -> None:
    payload = make_png(2, 1, 8, 2, [[(7, 7, 7), (251, 251, 251)]])
    path = tmp_path / "equal.PNG"
    path.write_bytes(payload)

    decoded = decode_png(path, confirm_relative_intensity=True)
    legacy = png_analysis.read_png(path, intensity_semantics_confirmed=True)

    assert decoded.image is not None
    assert np.array_equal(decoded.image.data, [[7, 251]])
    assert decoded.image.channels == legacy["channels"] == 3
    assert decoded.image.channels_identical is legacy["channels_identical"] is True
    assert decoded.image.metadata["png_color_type"] == legacy["color_type"] == 2
    assert legacy["samples"] == [7, 251]


def test_versioned_worker_uses_the_same_equal_channel_contract(tmp_path: Path) -> None:
    yy, xx = np.indices((64, 64))
    source = np.clip(20 + 220 * np.exp(-((xx - 32) ** 2 + (yy - 32) ** 2) / 80), 0, 255).astype(np.uint8)
    rows = source.tolist()
    payload = make_png(64, 64, 8, 2, rows)
    path = tmp_path / "worker equal channel.PNG"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    configuration = asdict(AnalysisConfiguration(
        AnalysisRegion(8, 0, 56, 64),
        background_region=AnalysisRegion(0, 0, 8, 64),
    ))

    messages = handle_request({
        "schema": "analysis-request-v1",
        "input": {"asset": {"path": str(path), "expected_sha256": digest}, "confirm_relative_intensity": True},
        "configuration": configuration,
        "output_strategy": {"work_directory": str(tmp_path / "assets"), "derived_format": "npy"},
    })

    assert messages[-1]["kind"] == "completed"
    input_metadata = messages[-1]["record"]["input"]
    assert input_metadata["sha256"] == digest
    assert input_metadata["bit_depth"] == 8
    assert input_metadata["channels"] == 3
    assert input_metadata["channels_identical"] is True
    assert input_metadata["metadata"]["png_color_type"] == 2


def test_png_contract_rejects_colour_and_invalid_inputs_without_mutating_source(tmp_path: Path) -> None:
    cases = {
        "rgb": (8, 2, [[(1, 2, 1)]]),
        "rgb16": (16, 2, [[(1, 1, 1)]]),
        "rgba": (8, 6, [[(1, 1, 1, 1)]]),
        "palette": (8, 3, [[1]]),
    }
    for name, (depth, color_type, rows) in cases.items():
        path = tmp_path / f"{name}.png"
        path.write_bytes(make_png(1, 1, depth, color_type, rows))
        before = path.read_bytes()
        outcome = decode_png(path, confirm_relative_intensity=True)
        assert outcome.image is None
        assert outcome.flow_status.value == "input_invalid"
        assert outcome.diagnostics and outcome.diagnostics[0]["code"] in {
            "rgb_channels_not_identical", "png_encoding_unsupported"
        }
        assert path.read_bytes() == before

    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(make_png(1, 1, 8, 0, [[9]])[:-7])
    outcome = decode_png(corrupt, confirm_relative_intensity=True)
    assert outcome.image is None
    assert outcome.flow_status.value == "input_decode_failed"
    assert outcome.diagnostics[0]["code"] == "png_decode_failed"


def test_png_hash_and_semantic_rejections_are_structured_and_read_only(tmp_path: Path) -> None:
    path = tmp_path / "input.png"
    payload = make_png(1, 1, 8, 0, [[9]])
    path.write_bytes(payload)
    before = (path.stat().st_mtime_ns, path.read_bytes())

    unconfirmed = decode_png(path)
    mismatch = decode_png(path, confirm_relative_intensity=True, expected_sha256="0" * 64)

    assert unconfirmed.diagnostics[0]["code"] == "encoding_semantic_unconfirmed"
    assert "确认" in unconfirmed.diagnostics[0]["message"]
    assert mismatch.diagnostics[0]["code"] == "input_hash_mismatch"
    assert "SHA-256" in mismatch.diagnostics[0]["message"]
    assert (path.stat().st_mtime_ns, path.read_bytes()) == before
