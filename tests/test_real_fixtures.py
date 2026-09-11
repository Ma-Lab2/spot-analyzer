from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from spot_analyzer import AnalysisConfiguration, AnalysisRegion, analyze
from spot_analyzer.input import decode_png


MANIFEST = Path(__file__).parents[1] / "docs" / "validation" / "issue-10-real-fixtures.json"


def _fixture_entries() -> list[dict[str, object]]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["fixtures"]


def test_real_fixture_manifest_records_traceable_identity_and_honest_unknowns() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert payload["audit"]["filesystem_timestamps_are_acquisition_evidence"] is False
    assert payload["evidence_scope"]["supported_claims"]
    assert payload["bounded_limitations"]
    for entry in payload["fixtures"]:
        assert len(entry["sha256"]) == 64
        assert entry["provenance"]["source_asset"]
        snapshot = entry["provenance"]["source_snapshot"]
        assert snapshot["recorded_in_commit"] == "51e1750fb90f4937386127643f6e9b5bab7cd902"
        assert snapshot["identity_basis"] == "relative_path_and_sha256"
        acquisition = entry["acquisition"]
        if entry["kind"] == "rgb_display_excluded":
            assert acquisition["evidence_status"] == "recovered_from_embedded_png_text"
            assert acquisition["acquired_at_timezone"] is None
        else:
            assert acquisition["instrument"] is None
            assert acquisition["acquired_at"] is None
            assert acquisition["metadata_version"] is None
            assert acquisition["evidence_status"] == "unrecoverable_from_checked_sources"


def test_documented_real_fixture_hashes_when_external_directory_is_available() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    root = Path(payload["root"])
    if not root.exists():
        pytest.skip("external fixture directory unavailable")

    for entry in _fixture_entries():
        path = root / str(entry["relative_path"])
        if not path.exists():
            pytest.fail(f"required fixture missing: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == entry["sha256"], path


@pytest.mark.parametrize(
    "entry",
    [entry for entry in _fixture_entries() if entry["kind"] != "rgb_display_excluded"],
    ids=lambda entry: str(entry["relative_path"]),
)
def test_real_grayscale_fixture_analysis_matches_status_and_reason_contract(entry: dict[str, object]) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    root = Path(payload["root"])
    path = root / str(entry["relative_path"])
    if not path.exists():
        pytest.skip("external fixture directory unavailable")

    decoded = decode_png(
        path,
        confirm_relative_intensity=True,
        expected_sha256=str(entry["sha256"]),
    )
    assert decoded.image is not None
    configuration = AnalysisConfiguration(
        region=AnalysisRegion(**entry["analysis_region"]),
        background_region=AnalysisRegion(**entry["background_region"]),
    )

    first = analyze(decoded.image, configuration)
    second = analyze(decoded.image, configuration)

    assert first.record is not None
    assert second.record is not None
    assert first.record.record_id != second.record.record_id
    assert first.record.analysis_fingerprint == second.record.analysis_fingerprint
    assert first.record.summary_status.value in entry["expected_summary_status"]
    assert first.record.diagnostics["reasons"] == second.record.diagnostics["reasons"]
    reasons = set(first.record.diagnostics["reasons"])
    assert set(entry["required_reason_codes"]).issubset(reasons)
    assert reasons.isdisjoint(entry["forbidden_reason_codes"])
    assert first.record.input_metadata["sha256"] == entry["sha256"]
    assert first.record.input_metadata["uri_hint"] == path.resolve().as_uri()
    assert first.record.input_metadata["read_status"] == "read"


def test_blank_real_png_is_not_reported_as_a_zero_measurement() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    root = Path(payload["root"])
    path = root / "20251016/2.png"
    if not path.exists():
        pytest.skip("external fixture directory unavailable")

    outcome = decode_png(path, confirm_relative_intensity=True)

    assert outcome.image is not None
    assert outcome.image.data.max() == 0
    assert outcome.image.data.min() == 0


def test_rgb_display_fixture_is_excluded_from_grayscale_input_regression() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    root = Path(payload["root"])
    path = root / "20260901/1.PNG"
    if not path.exists():
        pytest.skip("external fixture directory unavailable")

    outcome = decode_png(path, confirm_relative_intensity=True)

    assert outcome.image is not None
    assert outcome.image.channels == 3
    assert outcome.image.channels_identical is True
