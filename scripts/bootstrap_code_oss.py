#!/usr/bin/env python3
"""Materialize pinned Code-OSS source without adding another Git clone.

The destination is an explicit disposable build directory. This script never
touches an Aiko install, session registry, configured repositories, or vault.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
IDE = ROOT / "ide"


def safe_members(archive: tarfile.TarFile):
    """Allow only ordinary, relative archive entries before extraction.

    The pinned Code-OSS source has a symlink only in a terminal test fixture.
    Build source does not need it, and omitting all links is safer than trusting
    archive-controlled link targets during a desktop-product bootstrap.
    """
    for member in archive.getmembers():
        relative = Path(member.name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe upstream archive entry: {member.name}")
        if member.issym() or member.islnk():
            continue
        yield member


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    destination = args.destination.expanduser().resolve()
    if destination.exists() and any(destination.iterdir()):
        raise SystemExit(f"destination is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    upstream = json.loads((IDE / "upstream.json").read_text())
    commit = upstream["commit"]
    with tempfile.TemporaryDirectory(prefix="aiko-code-oss-") as temp:
        archive_path = Path(temp) / "vscode.tar.gz"
        with urlopen(f"https://codeload.github.com/microsoft/vscode/tar.gz/{commit}", timeout=60) as response, archive_path.open("wb") as output:
            shutil.copyfileobj(response, output)
        with tarfile.open(archive_path, "r:gz") as archive:
            members = list(safe_members(archive))
            roots = {Path(member.name).parts[0] for member in members if member.name}
            if len(roots) != 1:
                raise ValueError("unexpected Code-OSS archive root")
            archive.extractall(temp, members=members, filter="data")
        source = Path(temp) / next(iter(roots))
        for child in source.iterdir():
            shutil.move(str(child), destination / child.name)
    product_path = destination / "product.json"
    product = json.loads(product_path.read_text())
    product.update(json.loads((IDE / "overlay" / "product.json").read_text()))
    product_path.write_text(json.dumps(product, indent=2) + "\n")
    shutil.copytree(IDE / "extensions" / "aiko-orchestrator", destination / "extensions" / "aiko-orchestrator")
    (destination / "AIKO_UPSTREAM.json").write_text(json.dumps(upstream, indent=2) + "\n")
    print(f"Aiko IDE source prepared at {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
