from pathlib import Path


ROOT = Path(__file__).parents[1]
MAIN_WINDOW = ROOT / "src" / "SpotAnalysis.App" / "MainWindow.xaml.cs"


def test_cancelled_rerun_retains_previous_record() -> None:
    source = MAIN_WINDOW.read_text(encoding="utf-8")
    branch = source[source.index('case "cancelled":'):source.index('case "timeout":')]
    assert '_lastSuccessfulOutcome is not null' in branch
    assert 'ShowRetainedResult' in branch
    assert 'StatusText.Text = "Cancelled"' in branch


def test_timeout_rerun_retains_previous_record_without_generic_failure_mapping() -> None:
    source = MAIN_WINDOW.read_text(encoding="utf-8")
    branch = source[source.index('case "timeout":'):source.index('default:')]
    assert '_lastSuccessfulOutcome is not null' in branch
    assert 'ShowRetainedResult' in branch
    assert 'StatusText.Text = "Timed out"' in branch
    assert 'StatusText.Text = $"Failed' not in branch


def test_failed_rerun_retains_previous_record_and_success_replaces_it() -> None:
    source = MAIN_WINDOW.read_text(encoding="utf-8")
    success = source[source.index('case "success":'):source.index('case "cancelled":')]
    failure = source[source.index('ShowRetainedResult(StatusText.Text)') - 300:]
    assert '_lastSuccessfulOutcome = outcome' in success
    assert '_resultStale = false' in success
    assert '_lastSuccessfulOutcome is not null' in failure
    assert 'ShowRetainedResult(StatusText.Text)' in failure


def test_new_run_marks_prior_record_stale_while_processing() -> None:
    source = MAIN_WINDOW.read_text(encoding="utf-8")
    assert '_resultStale = _lastSuccessfulOutcome is not null' in source
    assert 'Processing current run; previous result is retained as stale.' in source
    assert 'var recordLabel = _resultStale ? "Previous result (stale)"' in source
