import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
HARNESS = ROOT / "tests" / "WorkspaceModelHarness" / "WorkspaceModelHarness.csproj"


def test_workspace_presentation_model_behaviour() -> None:
    completed = subprocess.run(
        ["dotnet", "run", "--project", str(HARNESS), "--configuration", "Release"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "Workspace presentation behavior passed" in completed.stdout
