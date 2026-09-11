import json
import subprocess
import sys
from pathlib import Path


WORKER = Path(__file__).parents[1] / "src" / "SpotAnalysis.Worker" / "worker.py"


def run(request: object) -> list[dict]:
    completed = subprocess.run(
        [sys.executable, str(WORKER)],
        input=json.dumps(request) + "\n",
        text=True,
        capture_output=True,
        check=True,
    )
    assert completed.stderr == ""
    return [json.loads(line) for line in completed.stdout.splitlines()]


def test_synthetic_request_has_started_and_terminal_success() -> None:
    messages = run(
        {
            "protocol_version": 1,
            "request_id": "smoke-1",
            "command": "analyze",
            "input": {"kind": "synthetic", "width": 16, "height": 16},
        }
    )
    assert [message["type"] for message in messages] == ["started", "terminal"]
    assert messages[0]["status"] == "processing"
    assert messages[1]["status"] == "success"
    assert messages[1]["result"]["pixel_count"] == 256


def test_invalid_input_is_structured_failure() -> None:
    messages = run(
        {
            "protocol_version": 1,
            "request_id": "smoke-2",
            "command": "analyze",
            "input": {"kind": "not-supported"},
        }
    )
    assert messages[-1]["type"] == "terminal"
    assert messages[-1]["status"] == "failure"
    assert messages[-1]["error"]["code"] == "invalid_input"
