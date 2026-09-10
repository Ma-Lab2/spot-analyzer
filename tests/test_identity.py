from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from spot_analyzer.identity import canonical_bytes, fingerprint, validate_golden_vectors
from spot_analyzer.validation import (
    _asset_semantics,
    _semantic_differences,
    run_identity_validation,
    run_issue10_validation,
)


ROOT = Path(__file__).parents[1]


def test_numpy_scalars_and_arrays_canonicalize_like_native_values() -> None:
    native = {"count": 3, "values": [1.5, 2.5]}
    numpy_payload = {"count": np.int64(3), "values": np.asarray([1.5, 2.5], dtype=np.float64)}
    assert canonical_bytes(native) == canonical_bytes(numpy_payload)
    assert fingerprint(native) == fingerprint(numpy_payload)


def test_nonfinite_values_remain_distinct() -> None:
    assert fingerprint({"value": float("nan")}) != fingerprint({"value": float("inf")})


def test_missing_golden_vectors_are_incomplete(tmp_path: Path) -> None:
    result = validate_golden_vectors(tmp_path / "missing.json")
    assert result["status"] == "incomplete"
    assert result["incomplete_reason"] == "golden_vector_artifact_unavailable"


def test_golden_vector_mismatch_is_failed_even_outside_formal_python(tmp_path: Path) -> None:
    payload = json.loads((ROOT / "docs/validation/issue-10-fingerprint-golden-vectors.json").read_text(encoding="utf-8"))
    payload["vectors"][0]["digest"] = "sha256-" + "0" * 64
    path = tmp_path / "mismatch.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = validate_golden_vectors(path)

    assert result["status"] == "failed"
    assert result["failures"][0]["code"] == "golden_vector_mismatch"
    assert "incomplete_reason" not in result


def test_committed_golden_vectors_are_checked_in_a_child_process() -> None:
    result = validate_golden_vectors(ROOT / "docs/validation/issue-10-fingerprint-golden-vectors.json")
    assert result["failures"] == []
    assert result["vector_count"] == 3
    assert result["status"] in {"passed", "incomplete"}


def test_semantic_difference_reports_nested_contract_field() -> None:
    differences = _semantic_differences(
        {"diagnostics": {"mask_statistics": {"true_count": 4}}},
        {"diagnostics": {"mask_statistics": {"true_count": 3}}},
    )
    assert differences == [{
        "path": "diagnostics.mask_statistics.true_count",
        "expected": 4,
        "actual": 3,
    }]


def test_derived_asset_semantics_ignores_run_identity_but_checks_content() -> None:
    first = [{"record_id": "first", "uri": "file:///first", "asset_id": "asset-a", "sha256": "abc"}]
    second = [{"record_id": "second", "uri": "file:///second", "asset_id": "asset-a", "sha256": "abc"}]
    assert _semantic_differences(_asset_semantics(first), _asset_semantics(second)) == []
    second[0]["sha256"] = "different"
    assert _semantic_differences(_asset_semantics(first), _asset_semantics(second))[0]["path"] == "[0].sha256"


def test_identity_validation_reports_golden_vectors_and_worker_parity() -> None:
    result = run_identity_validation(ROOT / "docs/validation/issue-10-fingerprint-golden-vectors.json")
    assert result["status"] in {"passed", "incomplete"}
    assert result["golden_vectors"]["failures"] == []
    assert result["worker_parity"]["status"] == "passed"


def test_issue10_validation_exposes_identity_section(monkeypatch) -> None:
    monkeypatch.setattr(
        "spot_analyzer.validation.run_performance_baseline",
        lambda **kwargs: {"formal_status": "incomplete", "passed": False, "workloads": []},
    )
    result = run_issue10_validation(manifests={}, seeds=(), golden_vector_path=ROOT / "missing-vectors.json")
    assert result["sections"]["identity"]["status"] == "incomplete"
    assert result["overall_status"] in {"incomplete", "failed"}
