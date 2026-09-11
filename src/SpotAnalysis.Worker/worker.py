"""Minimal Spot Analysis worker protocol for the WPF seam."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from typing import Any

# The packaged worker is launched by path from the WPF shell, so its repository
# root is not otherwise on sys.path during development.  Keep the protocol
# adapter here, but let the versioned contract delegate to the real core worker.
_WORKER_DIRECTORY = Path(__file__).resolve().parent
_ROOT_CANDIDATES = (_WORKER_DIRECTORY, Path(__file__).resolve().parents[2])
_ROOT = next(
    (candidate for candidate in _ROOT_CANDIDATES if (candidate / "spot_analyzer").is_dir()),
    _ROOT_CANDIDATES[-1],
)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from png_analysis import InputError, analyze_png
from spot_analyzer.worker import handle_request as handle_real_request

PROTOCOL_VERSION = 1


def send(message: dict[str, Any]) -> None:
    """Write exactly one protocol message to stdout."""
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def fail(request_id: str | None, code: str, message: str) -> None:
    send(
        {
            "protocol_version": PROTOCOL_VERSION,
            "type": "terminal",
            "status": "failure",
            "request_id": request_id,
            "error": {"code": code, "message": message},
        }
    )


def _real_worker_failure(message: str) -> list[dict[str, Any]]:
    return [
        {"schema": "analysis-event-v1", "kind": "started", "flow_status": "processing"},
        {
            "schema": "analysis-result-v1",
            "kind": "failed",
            "flow_status": "analysis_failed",
            "summary_status": None,
            "record": None,
            "metrics": None,
            "diagnostics": [{"code": "worker_unhandled_error", "message": message}],
        },
    ]


def handle(request: object) -> None:
    if not isinstance(request, dict):
        fail(None, "invalid_request", "request must be a JSON object")
        return

    # analysis-request-v1 is the shared CLI/WPF contract.  This adapter must
    # not decode arrays, estimate metrics, or reproduce quality gates: the
    # package worker owns input adaptation, core analysis, and result shaping.
    if request.get("schema") == "analysis-request-v1":
        try:
            messages = handle_real_request(request)
        except Exception as exc:  # Keep real-worker failures structured.
            messages = _real_worker_failure(str(exc))
        for message in messages:
            send(message)
        return

    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        fail(None, "invalid_request", "request_id is required")
        return
    if request.get("protocol_version") != PROTOCOL_VERSION:
        fail(request_id, "unsupported_protocol", "unsupported protocol_version")
        return
    if request.get("command") != "analyze":
        fail(request_id, "unsupported_command", "command must be analyze")
        return

    send(
        {
            "protocol_version": PROTOCOL_VERSION,
            "type": "started",
            "status": "processing",
            "request_id": request_id,
        }
    )
    payload = request.get("input")
    if isinstance(payload, dict) and payload.get("kind") == "synthetic":
        width = payload.get("width")
        height = payload.get("height")
        if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
            fail(request_id, "invalid_input", "synthetic width and height must be positive integers")
            return
        time.sleep(0.001)
        send({"protocol_version": PROTOCOL_VERSION, "type": "terminal", "status": "success",
              "request_id": request_id, "result": {"input_kind": "synthetic", "width": width,
              "height": height, "pixel_count": width * height}})
        return
    try:
        record = analyze_png(request)
    except InputError as exc:
        fail(request_id, exc.code, str(exc))
        return
    send({"protocol_version": PROTOCOL_VERSION, "type": "terminal", "status": "success",
          "request_id": request_id, "result": record})


def main() -> int:
    for line in sys.stdin:
        try:
            handle(json.loads(line))
        except json.JSONDecodeError:
            fail(None, "invalid_json", "stdin line is not valid JSON")
        except Exception as exc:  # Keep protocol failures structured.
            fail(None, "worker_error", str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
