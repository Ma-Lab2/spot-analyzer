"""Minimal Spot Analysis worker protocol for the WPF seam."""
from __future__ import annotations

import json
import sys
import time
from typing import Any

from png_analysis import InputError, analyze_png

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


def handle(request: object) -> None:
    if not isinstance(request, dict):
        fail(None, "invalid_request", "request must be a JSON object")
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
