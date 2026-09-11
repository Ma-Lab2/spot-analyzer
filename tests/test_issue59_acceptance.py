from pathlib import Path


ROOT = Path(__file__).parents[1]
APP = ROOT / "src" / "SpotAnalysis.App"


def test_issue59_uses_one_immutable_request_snapshot_for_summary_and_worker() -> None:
    request = (APP / "AnalysisRequest.cs").read_text(encoding="utf-8")
    window = (APP / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    worker = (APP / "WorkerClient.cs").read_text(encoding="utf-8")

    assert "public sealed record AnalysisRequest" in request
    assert "public string Summary" in request
    for section in ("input", "configuration", "output_strategy", "preprocessing", "model", "standard_profile", "algorithm_version"):
        assert f'["{section}"]' in request
    assert "SummaryText.Text = _pendingRequest.Summary" in window
    assert "var request = _pendingRequest;" in window
    assert "_workerClient.RunAsync(" in window
    assert "AnalysisRequest request" in worker


def test_issue59_run_gate_requires_confirmation_valid_snapshot_and_no_inflight_run() -> None:
    window = (APP / "MainWindow.xaml.cs").read_text(encoding="utf-8")

    gate = window[window.index("private void UpdateRunAvailability"):window.index("private void ConfigurationChanged")]
    assert "_inFlightRequest is null" in gate
    assert "_configurationConfirmed" in gate
    assert "_pendingRequest is not null" in gate
    assert "_pendingRequest.IsValid" in gate
