from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from spot_analyzer.identity import canonical_bytes, fingerprint, validate_golden_vectors
from spot_analyzer.validation import run_identity_validation, run_issue10_validation


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


def test_committed_golden_vectors_are_checked_in_a_child_process() -> None:
    result = validate_golden_vectors(ROOT / "docs/validation/issue-10-fingerprint-golden-vectors.json")
    assert result["failures"] == []
    assert result["vector_count"] == 3
    assert result["status"] in {"passed", "incomplete"}


def test_identity_validation_reports_golden_vectors_and_worker_parity() -> None:
    result = run_identity_validation(ROOT / "docs/validation/issue-10-fingerprint-golden-vectors.json")
    assert result["status"] in {"passed", "incomplete"}
    assert result["golden_vectors"]["failures"] == []
    assert result["worker_parity"]["status"] == "passed"


def test_issue10_validation_exposes_identity_section() -> None:
    result = run_issue10_validation(manifests={}, seeds=(), golden_vector_path=ROOT / "missing-vectors.json")
    assert result["sections"]["identity"]["status"] == "incomplete"
    assert result["overall_status"] in {"incomplete", "failed"}
