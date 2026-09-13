import subprocess
from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).parents[1]
APP = ROOT / "src" / "SpotAnalysis.App"
HARNESS = ROOT / "tests" / "WorkspaceModelHarness" / "WorkspaceModelHarness.csproj"
XAML_NS = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"


def test_prototype_a_layout_has_four_connected_regions() -> None:
    root = ET.parse(APP / "MainWindow.xaml").getroot()
    grid = root.find(f"{XAML_NS}Grid")
    assert grid is not None
    rows = grid.find(f"{XAML_NS}Grid.RowDefinitions")
    columns = grid.find(f"{XAML_NS}Grid.ColumnDefinitions")
    assert rows is not None and len(rows) == 3
    assert columns is not None and len(columns) == 3

    text = (APP / "MainWindow.xaml").read_text(encoding="utf-8")
    for marker in (
        "01 · 输入与配置",
        "05 · 执行分析",
        "Display image · input data",
        "Status &amp; results",
        "Explanation curves",
        "No analysis result yet",
    ):
        assert marker in text

    code = (APP / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    for connection in (
        "_workspaceItems.Add",
        "_workspace.Confirm",
        "item.Item.Presentation.Start",
        "_scheduler.Cancel",
        "_workspaceItems.Apply",
        "ExportResult_Click",
    ):
        assert connection in code


def test_workspace_flow_harness_covers_connected_state_transitions() -> None:
    completed = subprocess.run(
        ["dotnet", "run", "--project", str(HARNESS), "--configuration", "Release"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "Workspace presentation behavior passed" in completed.stdout
