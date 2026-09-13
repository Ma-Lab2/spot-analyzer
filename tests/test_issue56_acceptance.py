from pathlib import Path


ROOT = Path(__file__).parents[1]
MAIN_WINDOW = ROOT / "src" / "SpotAnalysis.App" / "MainWindow.xaml.cs"


def _scheduled_path() -> str:
    source = MAIN_WINDOW.read_text(encoding="utf-8")
    start = source.index("private async Task RunScheduledAsync")
    end = source.index("private async void OpenPng_Click", start)
    return source[start:end]


def test_cancelled_rerun_retains_previous_record() -> None:
    source = MAIN_WINDOW.read_text(encoding="utf-8")
    scheduled = _scheduled_path()
    assert '"cancelled" => "cancelled"' in scheduled
    assert "item.LastSuccessfulOutcome = outcome" in scheduled
    assert "item.LastSuccessfulOutcome = null" not in scheduled
    assert "var outcome = item.LastSuccessfulOutcome" in source


def test_timeout_rerun_retains_previous_record_without_generic_failure_mapping() -> None:
    source = MAIN_WINDOW.read_text(encoding="utf-8")
    scheduled = _scheduled_path()
    assert '"timeout" => "timeout"' in scheduled
    assert 'WorkspaceWorkflowStatus.TimedOut => "Timed out"' in source
    assert "item.LastSuccessfulOutcome = null" not in scheduled


def test_failed_rerun_retains_previous_record_and_success_replaces_it() -> None:
    scheduled = _scheduled_path()
    success = scheduled[scheduled.index('if (outcome.Status == "success")') :]
    assert "item.LastSuccessfulOutcome = outcome" in success
    assert "item.ResultStale = item.Item.Presentation.CurrentRecord?.IsStale != false" in success
    assert "item.LastSuccessfulOutcome = null" not in scheduled


def test_new_run_marks_prior_record_stale_while_processing() -> None:
    source = MAIN_WINDOW.read_text(encoding="utf-8")
    scheduled = _scheduled_path()
    assert "item.ResultStale = item.LastSuccessfulOutcome is not null" in scheduled
    assert "Processing current run; previous result is retained as stale." in scheduled
    assert 'var recordLabel = _resultStale ? "Previous result (stale)"' in source
