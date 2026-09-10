"""Cross-process canonical identity and golden-vector validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Mapping

import numpy as np
import rfc8785


CANONICALIZER_VERSION = "rfc8785-python-0.1.4"
IDENTITY_SECTION = "identity"


def _jsonable(value: Any) -> Any:
    """Normalize supported Python and NumPy values without collapsing non-finite values."""
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return [_jsonable(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return _jsonable(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return {"__nonfinite_float__": value.hex()}
    return value


def canonical_bytes(value: Any) -> bytes:
    """Return RFC 8785 canonical UTF-8 bytes for a supported JSON payload."""
    return rfc8785.dumps(_jsonable(value))


def fingerprint(value: Any) -> str:
    """Return the stable SHA-256 identity for a canonical payload."""
    return "sha256-" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _cli() -> int:
    payload = json.load(sys.stdin)
    canonical = canonical_bytes(payload)
    sys.stdout.write(json.dumps({"canonical_hex": canonical.hex(), "digest": fingerprint(payload)}))
    return 0


@dataclass(frozen=True)
class GoldenVectorResult:
    status: str
    vector_count: int
    failures: tuple[dict[str, Any], ...] = ()
    incomplete_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": IDENTITY_SECTION,
            "status": self.status,
            "vector_count": self.vector_count,
            "failures": list(self.failures),
            "canonicalizer_version": CANONICALIZER_VERSION,
            "python": platform.python_version(),
        }
        if self.incomplete_reason:
            result["incomplete_reason"] = self.incomplete_reason
        return result


def _run_subprocess(payload: Any) -> dict[str, Any]:
    completed = subprocess.run(
        (sys.executable, "-m", "spot_analyzer.identity", "--canonicalize"),
        input=json.dumps(_jsonable(payload), ensure_ascii=False, allow_nan=False),
        text=True,
        capture_output=True,
        check=True,
    )
    result = json.loads(completed.stdout)
    if not isinstance(result, dict):
        raise ValueError("identity subprocess returned a non-object")
    return result


def validate_golden_vectors(path: str | Path) -> dict[str, Any]:
    """Validate expected canonical bytes and digests in a separate process."""
    path = Path(path)
    if not path.is_file():
        return GoldenVectorResult(
            "incomplete", 0, incomplete_reason="golden_vector_artifact_unavailable"
        ).to_dict()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        vectors = payload["vectors"] if isinstance(payload, dict) else payload
        if not isinstance(vectors, list):
            raise ValueError("vectors must be a list")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        return GoldenVectorResult("failed", 0, ({"code": "golden_vector_invalid", "message": str(error)},)).to_dict()

    failures: list[dict[str, Any]] = []
    for index, vector in enumerate(vectors):
        if not isinstance(vector, dict) or "payload" not in vector:
            failures.append({"index": index, "code": "vector_invalid"})
            continue
        try:
            expected_canonical = vector["canonical_hex"]
            expected_digest = vector["digest"]
            actual_canonical = canonical_bytes(vector["payload"]).hex()
            child = _run_subprocess(vector["payload"])
            checks = {
                "canonical": actual_canonical == expected_canonical == child.get("canonical_hex"),
                "digest": fingerprint(vector["payload"]) == expected_digest == child.get("digest"),
            }
            if not all(checks.values()):
                failures.append({"index": index, "code": "golden_vector_mismatch", "checks": checks})
        except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
            failures.append({"index": index, "code": "vector_execution_failed", "message": str(error)})
    status = "passed" if vectors and not failures else "failed"
    if sys.version_info[:2] != (3, 12):
        status = "incomplete"
    result = GoldenVectorResult(status, len(vectors), tuple(failures))
    if status == "incomplete":
        result = GoldenVectorResult(status, len(vectors), tuple(failures), "formal_validation_requires_python_3_12")
    return result.to_dict()


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--canonicalize":
        raise SystemExit(_cli())
    raise SystemExit("usage: python -m spot_analyzer.identity --canonicalize")
