from pathlib import Path


ROOT = Path(__file__).parents[1]
APP = ROOT / "src" / "SpotAnalysis.App"


def test_wpf_exposes_multi_image_controls_and_selection_routing() -> None:
    xaml = (APP / "MainWindow.xaml").read_text(encoding="utf-8")
    code = (APP / "MainWindow.xaml.cs").read_text(encoding="utf-8")

    for marker in (
        'x:Name="ImageItemsList"',
        'AutomationProperties.Name="图片工作项列表"',
        'x:Name="LockRoiCheck"',
        'x:Name="ApplyCalibrationBatchButton"',
        'SelectionChanged="ImageItemsList_SelectionChanged"',
    ):
        assert marker in xaml
    assert "Multiselect = true" in code
    assert "WorkspaceAnalysisPriority.Current" in code
    assert "WorkspaceAnalysisPriority.Background" in code
    assert "Dispatcher.InvokeAsync" in code
    scheduled = code.index("private async Task RunScheduledAsync")
    rejected = code.index("if (!accepted)", scheduled)
    ui_mutation = code.index("item.LastOutcome = outcome", scheduled)
    assert rejected < ui_mutation, "late worker events must be rejected before per-item UI state changes"
    assert "SelectComboBoxValue(AdvancedFiltering" in code
    assert "SelectComboBoxValue(AdvancedDpc" in code
    assert "Task.Run" in (APP / "PngInput.cs").read_text(encoding="utf-8")


def test_multi_image_state_and_scheduling_stay_in_client_boundary() -> None:
    workspace = (APP / "MultiImageWorkspace.cs").read_text(encoding="utf-8")
    worker = (ROOT / "src" / "SpotAnalysis.Worker" / "worker.py").read_text(encoding="utf-8")

    for marker in (
        "class MultiImageWorkspaceModel",
        "class MultiImageWorkspaceItem",
        "class WorkspaceAnalysisScheduler",
        "WorkspaceAnalysisPriority",
        "SetAutomaticDraft",
        "LockRoiForSubsequent",
        "ApplyCalibrationToBatch",
    ):
        assert marker in workspace
    assert "spot_analyzer.worker import handle_request" in worker
    assert "MultiImage" not in worker
    assert "WorkspaceAnalysisScheduler" not in worker
