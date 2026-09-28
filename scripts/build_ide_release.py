#!/usr/bin/env python3
"""Build a reproducible native Aiko IDE artifact from the pinned Code-OSS tree.

This command intentionally operates in a new, explicit directory.  It never
reads or writes Aiko runtime state, a workspace, a daemon database, or secrets.
Use ``dev`` for an unsigned self-hosting build, ``candidate`` for an unsigned
ZIP suitable for QA, and ``release`` only on a macOS release host that has an
Apple signing identity and a notarytool keychain profile configured.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform as host_platform
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
IDE = ROOT / "ide"
SUPPORTED = {"darwin"}


def run(argv: list[str], *, cwd: Path) -> None:
    print("+", " ".join(argv), flush=True)
    subprocess.run(argv, cwd=cwd, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_empty_destination(destination: Path) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"destination must be absent or empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)


def source_tree(destination: Path) -> Path:
    tree = destination / "source"
    run([sys.executable, str(ROOT / "scripts" / "bootstrap_code_oss.py"),
         "--destination", str(tree)], cwd=ROOT)
    return tree


def package_task(system: str, arch: str) -> str:
    if system not in SUPPORTED:
        raise ValueError(f"unsupported platform {system!r}; supported: {', '.join(sorted(SUPPORTED))}")
    if arch not in {"arm64", "x64"}:
        raise ValueError("arch must be arm64 or x64")
    return f"vscode-{system}-{arch}-min"


def find_app(source: Path) -> Path:
    apps = sorted(path for path in source.rglob("*.app") if path.is_dir())
    if len(apps) != 1:
        rendered = ", ".join(str(path.relative_to(source)) for path in apps) or "none"
        raise RuntimeError(f"expected exactly one packaged .app, found: {rendered}")
    return apps[0]


def make_zip(app: Path, artifacts: Path, version: str, arch: str) -> Path:
    artifacts.mkdir(parents=True, exist_ok=True)
    archive = artifacts / f"Aiko-IDE-{version}-darwin-{arch}.zip"
    # ditto is the correct macOS archiver for bundles and resource forks.
    run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)], cwd=app.parent)
    return archive


def sign_and_notarize(app: Path, archive: Path, source: Path, *, version: str, arch: str) -> Path:
    identity = os.environ.get("AIKO_CODESIGN_IDENTITY")
    profile = os.environ.get("AIKO_NOTARY_PROFILE")
    if not identity or not profile:
        raise ValueError("release mode requires AIKO_CODESIGN_IDENTITY and AIKO_NOTARY_PROFILE")
    entitlements = IDE / "release" / "darwin" / "entitlements.plist"
    if not entitlements.is_file():
        raise RuntimeError(f"missing release entitlements: {entitlements}")
    run(["codesign", "--force", "--deep", "--options", "runtime", "--timestamp",
         "--entitlements", str(entitlements), "--sign", identity, str(app)], cwd=source)
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)], cwd=source)
    archive.unlink(missing_ok=True)
    archive = make_zip(app, archive.parent, version, arch)
    run(["xcrun", "notarytool", "submit", str(archive), "--keychain-profile", profile, "--wait"], cwd=source)
    run(["xcrun", "stapler", "staple", str(app)], cwd=source)
    archive.unlink(missing_ok=True)
    # Re-archive after stapling so the published ZIP contains the ticket.
    return make_zip(app, archive.parent, version, arch)


def write_manifest(artifacts: Path, archive: Path, *, version: str, arch: str, mode: str) -> Path:
    upstream = json.loads((IDE / "upstream.json").read_text())
    manifest = {
        "product": "Aiko IDE",
        "version": version,
        "mode": mode,
        "platform": "darwin",
        "arch": arch,
        "upstream": upstream,
        "artifact": archive.name,
        "sha256": sha256(archive),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = artifacts / f"{archive.name}.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    (artifacts / "SHA256SUMS").write_text(f"{manifest['sha256']}  {archive.name}\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("dev", "candidate", "release"))
    parser.add_argument("--destination", type=Path, required=True,
                        help="empty directory in which source and artifacts are created")
    parser.add_argument("--version", default="0.0.0-dev", help="release version used in artifact names")
    parser.add_argument("--arch", default=host_platform.machine().replace("aarch64", "arm64"),
                        choices=("arm64", "x64"))
    parser.add_argument("--run", action="store_true", help="launch the unsigned dev app after it builds")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        raise SystemExit("Aiko IDE release builds currently run on native macOS only")
    if args.mode == "dev" and args.version != "0.0.0-dev":
        raise SystemExit("dev builds use version 0.0.0-dev; use candidate for versioned artifacts")
    if args.mode != "dev" and args.version == "0.0.0-dev":
        raise SystemExit("candidate/release builds require --version")
    clean_empty_destination(args.destination)
    source = source_tree(args.destination)
    run(["npm", "ci"], cwd=source)
    if args.mode == "dev":
        run(["npm", "run", "gulp", "compile"], cwd=source)
        if args.run:
            run(["./scripts/code.sh"], cwd=source)
        print(f"Aiko IDE development source is ready: {source}")
        return 0
    run(["npm", "run", "gulp", package_task("darwin", args.arch)], cwd=source)
    app = find_app(source)
    archive = make_zip(app, args.destination / "artifacts", args.version, args.arch)
    if args.mode == "release":
        archive = sign_and_notarize(app, archive, source, version=args.version, arch=args.arch)
    manifest = write_manifest(args.destination / "artifacts", archive,
                              version=args.version, arch=args.arch, mode=args.mode)
    print(f"Aiko IDE artifact: {archive}\nManifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
