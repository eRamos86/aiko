import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("build_ide_release", ROOT / "scripts" / "build_ide_release.py")
release = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(release)


def test_release_platform_task_is_explicit_and_native():
    assert release.package_task("darwin", "arm64") == "vscode-darwin-arm64-min"
    assert release.package_task("darwin", "x64") == "vscode-darwin-x64-min"
    try:
        release.package_task("linux", "x64")
    except ValueError as exc:
        assert "unsupported platform" in str(exc)
    else:
        raise AssertionError("non-native release target must not be accepted")


def test_manifest_is_hash_bound_to_the_artifact(tmp_path):
    archive = tmp_path / "Aiko-IDE-1.0.0-darwin-arm64.zip"
    archive.write_bytes(b"aiko release artifact")
    manifest_path = release.write_manifest(tmp_path, archive, version="1.0.0", arch="arm64", mode="candidate")
    manifest = json.loads(manifest_path.read_text())
    assert manifest["product"] == "Aiko IDE"
    assert manifest["artifact"] == archive.name
    assert manifest["sha256"] == release.sha256(archive)
    assert "94e8ae2b28cb5cc932b86e1070569c4463565c37" == manifest["upstream"]["commit"]
    assert archive.name in (tmp_path / "SHA256SUMS").read_text()


def test_archive_source_tree_gets_only_the_git_metadata_upstream_requires(tmp_path, monkeypatch):
    calls = []

    def fake_run(argv, *, cwd):
        calls.append((argv, cwd))

    monkeypatch.setattr(release, "run", fake_run)
    tree = release.source_tree(tmp_path)
    assert tree == tmp_path / "source"
    assert calls[1] == (["git", "init", "--quiet"], tree)
    assert calls[2] == (["git", "config", "pull.rebase", "merges"], tree)
