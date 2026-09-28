import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_code_oss_source_is_pinned_and_attributed():
    upstream = json.loads((ROOT / "ide" / "upstream.json").read_text())
    assert upstream["upstream"] == "https://github.com/microsoft/vscode.git"
    assert len(upstream["commit"]) == 40
    assert upstream["license"] == "MIT"


def test_ide_extension_exposes_durable_agent_commands():
    manifest = json.loads((ROOT / "ide" / "extensions" / "aiko-orchestrator" / "package.json").read_text())
    commands = {item["command"] for item in manifest["contributes"]["commands"]}
    assert {"aiko.newGoal", "aiko.openAgent", "aiko.attachSession", "aiko.messageSession",
            "aiko.decideApproval", "aiko.browseSkills"} <= commands
    assert manifest["contributes"]["configuration"]["properties"]["aiko.agent.targets"]["default"] == ["local"]
    source = (ROOT / "ide" / "extensions" / "aiko-orchestrator" / "extension.js").read_text()
    assert "context.secrets" in source
    assert "/approvals" in source
    assert "aiko skills" not in source  # CLI path is configured, never shell-interpolated.
    assert "poopmachine" not in source
