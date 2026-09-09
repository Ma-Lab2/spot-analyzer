from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from io import BytesIO
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote, urlparse

import pytest

import numpy as np
from PIL import Image

from spot_analyzer import AnalysisConfiguration, AnalysisRegion, InputImage, analyze, decode_png
from spot_analyzer.report import ReportSpecification, prepare_report, write_report
from spot_analyzer.synthetic import generate_scene
from spot_analyzer.worker import handle_request


def png_bytes(array: np.ndarray, mode: str = "L") -> bytes:
    buffer = BytesIO()
    image = Image.fromarray(array)
    assert image.mode == mode
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def json_value(value: object) -> object:
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, Mapping):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    return value


def worker_configuration() -> dict[str, object]:
    return asdict(
        AnalysisConfiguration(
            AnalysisRegion(64, 64, 128, 128),
            background_region=AnalysisRegion(32, 64, 32, 128),
        )
    )


def test_png_adapter_requires_explicit_semantic_confirmation_when_metadata_is_missing() -> None:
    payload = png_bytes(np.zeros((4, 4), dtype=np.uint8))

    rejected = decode_png(payload)
    accepted = decode_png(payload, confirm_relative_intensity=True)

    assert rejected.image is None
    assert rejected.diagnostics[0]["code"] == "encoding_semantic_unconfirmed"
    assert accepted.image is not None
    assert accepted.image.bit_depth == 8
    assert accepted.image.channels == 1
    assert accepted.image.sha256


def test_png_adapter_accepts_identical_rgb_and_preserves_conversion_fact() -> None:
    array = np.zeros((64, 64), dtype=np.uint8)
    array[32, 32] = 100
    rgb = np.repeat(array[..., None], 3, axis=2)
    payload = png_bytes(rgb, mode="RGB")

    outcome = decode_png(payload, confirm_relative_intensity=True)

    assert outcome.image is not None
    assert outcome.image.channels == 3
    assert outcome.image.channels_identical is True
    assert outcome.image.metadata["format"] == "PNG"
    assert outcome.image.uri_hint == "memory://png"


def test_png_adapter_preserves_16_bit_samples_and_png_byte_order() -> None:
    array = np.array([[0, 1], [32768, 65535]], dtype=np.uint16)
    payload = png_bytes(array, mode="I;16")

    outcome = decode_png(payload, confirm_relative_intensity=True)

    assert outcome.image is not None
    assert outcome.image.bit_depth == 16
    assert outcome.image.byte_order == "big"
    assert outcome.image.data.dtype == np.float64
    assert np.array_equal(outcome.image.data, array)
    assert outcome.image.metadata["png_bit_depth"] == 16


def test_png_adapter_rejects_expected_hash_mismatch() -> None:
    payload = png_bytes(np.zeros((4, 4), dtype=np.uint8))

    outcome = decode_png(payload, confirm_relative_intensity=True, expected_sha256="0" * 64)

    assert outcome.image is None
    assert outcome.diagnostics[0]["code"] == "input_hash_mismatch"


def test_png_adapter_distinguishes_decode_failure() -> None:
    outcome = decode_png(b"\x89PNG\r\n\x1a\ntruncated", confirm_relative_intensity=True)

    assert outcome.image is None
    assert outcome.flow_status.value == "input_decode_failed"
    assert outcome.diagnostics[0]["code"] == "png_decode_failed"


def test_png_adapter_rejects_non_png_payload() -> None:
    buffer = BytesIO()
    Image.fromarray(np.zeros((4, 4), dtype=np.uint8), mode="L").save(buffer, format="TIFF")

    outcome = decode_png(buffer.getvalue(), confirm_relative_intensity=True)

    assert outcome.image is None
    assert outcome.diagnostics[0]["code"] == "not_png"


def test_png_adapter_rejects_non_identical_rgb_channels() -> None:
    array = np.zeros((4, 4, 3), dtype=np.uint8)
    array[1, 1, 0] = 4
    payload = png_bytes(array, mode="RGB")

    outcome = decode_png(payload, confirm_relative_intensity=True)

    assert outcome.image is None
    assert outcome.diagnostics[0]["code"] == "rgb_channels_not_identical"


def test_worker_asset_request_validates_hash_and_matches_direct_core(tmp_path) -> None:
    scene = generate_scene("gaussian_circular")
    payload = png_bytes(scene.input_array.astype(np.uint8))
    path = tmp_path / "input.png"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    decoded = decode_png(path, confirm_relative_intensity=True, expected_sha256=digest)
    assert decoded.image is not None
    config = AnalysisConfiguration(
        AnalysisRegion(64, 64, 128, 128),
        background_region=AnalysisRegion(32, 64, 32, 128),
    )
    direct = analyze(decoded.image, config)
    request = {
        "schema": "analysis-request-v1",
        "input": {
            "asset": {"path": str(path), "expected_sha256": digest},
            "confirm_relative_intensity": True,
        },
        "configuration": worker_configuration(),
        "output_strategy": {
            "work_directory": str(tmp_path / "worker-assets"),
            "derived_format": "npy",
        },
        "display": {"colormap": "magma", "stretch": "log", "zoom": 4.0},
    }

    messages = handle_request(request)

    assert messages[-1]["kind"] == "completed"
    worker_record = messages[-1]["record"]
    assert worker_record["analysis_fingerprint"] == direct.record.analysis_fingerprint
    assert worker_record["input"]["sha256"] == digest
    assert worker_record["metrics"] == direct.record.reportable_metrics()
    assert worker_record["diagnostics"] == json_value(direct.record.diagnostics)
    assert {asset["kind"] for asset in worker_record["derived_assets"]} >= {
        "corrected_intensity",
        "gaussian_fit",
        "fit_residual",
        "measurement_mask",
    }
    assert all(asset["sha256"] for asset in worker_record["derived_assets"])
    assert all(asset["uri"].startswith("file:") for asset in worker_record["derived_assets"])
    assert all(asset["generation_parameters"]["allow_pickle"] is False for asset in worker_record["derived_assets"])
    for asset in worker_record["derived_assets"]:
        asset_path = Path(unquote(urlparse(asset["uri"]).path).lstrip("/"))
        assert asset_path.exists()
        assert hashlib.sha256(asset_path.read_bytes()).hexdigest() == asset["sha256"]


def test_worker_rejects_inline_image_arrays() -> None:
    request = {
        "schema": "analysis-request-v1",
        "input": {"data": [[0, 1], [2, 3]]},
        "configuration": {},
    }

    messages = handle_request(request)

    assert messages[0]["kind"] == "started"
    assert messages[-1]["schema"] == "analysis-result-v1"
    assert messages[-1]["kind"] == "failed"
    assert messages[-1]["record"] is None
    assert messages[-1]["metrics"] is None
    assert messages[-1]["summary_status"] is None
    assert messages[-1]["diagnostics"][0]["code"] == "input_asset_required"


def test_worker_rejects_incomplete_configuration_snapshot(tmp_path) -> None:
    path = tmp_path / "input.png"
    payload = png_bytes(np.zeros((8, 8), dtype=np.uint8))
    path.write_bytes(payload)
    incomplete_configuration = worker_configuration()
    incomplete_configuration["preprocessing"] = {}
    request = {
        "schema": "analysis-request-v1",
        "input": {
            "asset": {
                "path": str(path),
                "expected_sha256": hashlib.sha256(payload).hexdigest(),
            },
            "confirm_relative_intensity": True,
        },
        "configuration": incomplete_configuration,
        "output_strategy": {
            "work_directory": str(tmp_path / "worker-assets"),
            "derived_format": "npy",
        },
    }

    messages = handle_request(request)

    assert messages[-1]["kind"] == "failed"
    assert messages[-1]["flow_status"] == "parameter_invalid"
    assert "configuration.preprocessing snapshot is incomplete" in messages[-1]["diagnostics"][0]["message"]


def test_worker_asset_request_rejects_hash_mismatch(tmp_path) -> None:
    path = tmp_path / "input.png"
    path.write_bytes(png_bytes(np.zeros((8, 8), dtype=np.uint8)))
    request = {
        "schema": "analysis-request-v1",
        "input": {
            "asset": {"path": str(path), "expected_sha256": "0" * 64},
            "confirm_relative_intensity": True,
        },
        "configuration": {},
    }

    messages = handle_request(request)

    assert messages[0]["kind"] == "started"
    assert messages[-1]["kind"] == "failed"
    assert messages[-1]["flow_status"] == "input_invalid"
    assert messages[-1]["diagnostics"][0]["code"] == "input_hash_mismatch"


def test_worker_process_stdout_is_pure_ndjson(tmp_path) -> None:
    scene = generate_scene("gaussian_circular")
    path = tmp_path / "input.png"
    payload = png_bytes(scene.input_array.astype(np.uint8))
    path.write_bytes(payload)
    request = {
        "schema": "analysis-request-v1",
        "input": {
            "asset": {
                "path": str(path),
                "expected_sha256": hashlib.sha256(payload).hexdigest(),
            },
            "confirm_relative_intensity": True,
        },
        "configuration": worker_configuration(),
        "output_strategy": {
            "work_directory": str(tmp_path / "worker-assets"),
            "derived_format": "npy",
        },
    }

    completed = subprocess.run(
        [sys.executable, "-m", "spot_analyzer.worker"],
        input=json.dumps(request) + "\n",
        text=True,
        capture_output=True,
        check=False,
        cwd=Path(__file__).parents[1],
        timeout=60,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    messages = [json.loads(line) for line in completed.stdout.splitlines()]
    assert [message["kind"] for message in messages] == ["started", "completed"]
    assert all(message["schema"] in {"analysis-event-v1", "analysis-result-v1"} for message in messages)


def test_report_shares_record_id_and_never_overwrites_existing_name(tmp_path) -> None:
    scene = generate_scene("gaussian_circular")
    config = AnalysisConfiguration(AnalysisRegion(64, 64, 128, 128), background_region=AnalysisRegion(32, 64, 32, 128))
    outcome = analyze(InputImage(scene.input_array, bit_depth=8, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True), config)
    assert outcome.record is not None
    specification = ReportSpecification("png", "中文报告 / clean spot", tmp_path)
    package = prepare_report(outcome.record, specification)

    first = write_report(package, specification)
    second = write_report(package, specification)

    assert first.path is not None and first.path.exists()
    assert second.path is not None and second.path.exists()
    assert first.path != second.path
    assert first.record_id == second.record_id == outcome.record.record_id
    assert first.path.read_bytes() != b""
    with Image.open(first.path) as rendered:
        assert rendered.width >= 1200
        assert rendered.height >= 800
        pixels = np.asarray(rendered.convert("RGB"))
        assert np.any(np.all(pixels == (255, 0, 0), axis=2))
        assert np.any(np.all(pixels == (0, 255, 0), axis=2))
    assert first.sha256 == hashlib.sha256(first.path.read_bytes()).hexdigest()
    assert package.analysis_fingerprint == outcome.record.analysis_fingerprint
    assert set(package.visuals) >= {
        "输入图像",
        "校正强度图",
        "高斯拟合",
        "拟合残差",
        "测量有效 mask",
    }
    assert "fit_initial_parameters" not in package.sections["diagnostics"]["fit"]
    assert package.sections["input"]["sha256"] == outcome.record.input_metadata["sha256"]
    assert package.sections["configuration"]["region"] == {
        "x": 64,
        "y": 64,
        "width": 128,
        "height": 128,
        "confirmed": True,
    }
    with pytest.raises(TypeError):
        package.sections["summary"]["flow_status"] = "forged"
    with pytest.raises(ValueError, match="does not match"):
        write_report(package, ReportSpecification("pdf", "other-name", tmp_path))
    pdf_specification = ReportSpecification("pdf", "clean-spot-pdf", tmp_path)
    pdf = write_report(prepare_report(outcome.record, pdf_specification), pdf_specification)
    assert pdf.path is not None and pdf.path.suffix == ".pdf" and pdf.path.exists()
    assert pdf.sha256 == hashlib.sha256(pdf.path.read_bytes()).hexdigest()


def test_concurrent_report_exports_reserve_distinct_names(tmp_path) -> None:
    scene = generate_scene("gaussian_circular")
    outcome = analyze(
        InputImage(scene.input_array, bit_depth=8, encoding_semantic="relative_intensity_code", encoding_semantic_confirmed=True),
        AnalysisConfiguration(
            AnalysisRegion(64, 64, 128, 128),
            background_region=AnalysisRegion(32, 64, 32, 128),
        ),
    )
    assert outcome.record is not None
    specification = ReportSpecification("png", "concurrent-report", tmp_path)
    package = prepare_report(outcome.record, specification)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: write_report(package, specification), range(8)))

    paths = [result.path for result in results]
    assert all(path is not None and path.exists() for path in paths)
    assert len(set(paths)) == 8
    assert all(result.record_id == outcome.record.record_id for result in results)
    assert all(result.sha256 for result in results)
