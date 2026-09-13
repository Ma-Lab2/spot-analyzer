"""Structural acceptance checks for the Prototype A WPF presentation shell.

These tests intentionally inspect semantic structure and resource usage rather than
pixel snapshots, so layout regressions remain actionable across DPI settings.
"""

from pathlib import Path
import re
import xml.etree.ElementTree as ET


ROOT = Path(__file__).parents[1]
APP = ROOT / "src" / "SpotAnalysis.App"
XAML_NS = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"


def test_theme_resources_are_shared_by_the_workspace() -> None:
    app = (APP / "App.xaml").read_text(encoding="utf-8")
    theme = (APP / "Theme.xaml").read_text(encoding="utf-8")
    window = (APP / "MainWindow.xaml").read_text(encoding="utf-8")

    assert 'Source="Theme.xaml"' in app
    for token in (
        "ColorWorkspace",
        "ColorPanel",
        "ColorInk",
        "ColorAccent",
        "ColorFocus",
        "ColorSuccess",
        "ColorCaution",
        "ColorInvalid",
        "ColorUnavailable",
    ):
        assert f'x:Key="{token}"' in theme
    assert 'DynamicResource ColorAccent' in window
    assert 'DynamicResource ColorPanel' in window
    assert not re.search(r'#[0-9A-Fa-f]{6,8}', window), "screen colors must stay in Theme.xaml"


def test_workspace_preserves_four_regions_and_independent_surfaces() -> None:
    root = ET.parse(APP / "MainWindow.xaml").getroot()
    grid = root.find(f"{XAML_NS}Grid")
    assert grid is not None
    rows = grid.find(f"{XAML_NS}Grid.RowDefinitions")
    columns = grid.find(f"{XAML_NS}Grid.ColumnDefinitions")
    assert rows is not None and len(rows) == 3
    assert columns is not None and len(columns) == 3

    text = (APP / "MainWindow.xaml").read_text(encoding="utf-8")
    for region in ("ConfigurationRegion", "ImageRegion", "ResultsRegion", "CurvesRegion"):
        assert f'x:Name="{region}"' in text
    assert text.count("<ScrollViewer") >= 2
    assert text.count("<GridSplitter") >= 1
    assert 'ColumnDefinition Width="*" MinWidth="420"' in text
    for marker in (
        "01 · 输入与配置",
        "05 · 执行分析",
        "Display image · input data",
        "Status &amp; results",
        "Explanation curves",
        "No analysis result yet",
    ):
        assert marker in text


def test_keyboard_path_and_status_language_are_explicit() -> None:
    text = (APP / "MainWindow.xaml").read_text(encoding="utf-8")
    for tab_index in range(22):
        assert f'TabIndex="{tab_index}"' in text
    for name in (
        "OpenInputButton",
        "ConfirmConfigurationButton",
        "RunAnalysisButton",
        "CancelAnalysisButton",
        "ReportNameText",
        "ExportResultButton",
    ):
        assert f'AutomationProperties.Name="' in text[text.index(f'x:Name="{name}"') :]
    for status in ("valid", "caution", "invalid", "unavailable", "stale", "recompute"):
        assert status in text.lower() or status in (APP / "MainWindow.xaml.cs").read_text(encoding="utf-8").lower()
