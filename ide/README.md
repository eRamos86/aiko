# Aiko IDE

Aiko IDE is a branded Code-OSS distribution. It keeps the editor platform
developers expect and puts Aiko orchestration beside it: goals, durable worker
sessions, approvals, skills, and explicit local/server target selection.

`upstream.json` pins the exact Code-OSS source revision. `overlay/product.json`
changes only product identity and never carries credentials. The Aiko extension
talks to `aikod` using the same authenticated HTTP API as the CLI; bearer tokens
live in Code-OSS SecretStorage, never in workspace settings or source control.

Run `python scripts/bootstrap_code_oss.py --destination /path/to/build/aiko-ide`.
It downloads the pinned upstream archive, applies the product overlay, and
installs the Aiko built-in extension. The source directory is a disposable build
directory and must never contain Aiko state, sessions, credentials, or user
workspaces.

Closing or updating the IDE does not stop workers: they are daemon-owned tmux
sessions and can be returned to from Aiko IDE, the Textual TUI, or the CLI.

## Builds and releases

The bootstrap command creates source only. Use the release builder from a clean
checkout and an empty destination; it creates a disposable Code-OSS source tree
below that destination and never reads Aiko user data or daemon state.

```bash
# Self-host locally on an Apple Silicon Mac (unsigned, no distributable artifact).
python scripts/build_ide_release.py dev --destination /tmp/aiko-ide-dev --run

# Create an unsigned ZIP plus SHA-256 checksum and provenance manifest for QA.
python scripts/build_ide_release.py candidate --version 1.0.0-rc.1 \
  --destination /tmp/aiko-ide-candidate --arch arm64
```

`release` is intentionally stricter: it needs a native macOS release host with
an Apple Developer ID identity in its keychain and a `notarytool` keychain
profile. Set `AIKO_CODESIGN_IDENTITY` and `AIKO_NOTARY_PROFILE` only in that
release environment, then use:

```bash
python scripts/build_ide_release.py release --version 1.0.0 \
  --destination /tmp/aiko-ide-release --arch arm64
```

The release builder validates the app signature, notarizes the ZIP, staples the
notarization ticket into the app, then regenerates the published ZIP. It writes
`SHA256SUMS` and an adjacent JSON provenance manifest naming the exact Code-OSS
revision. Do not put certificates, notary credentials, Aiko daemon tokens, or
workspaces in this repository or a build destination.

## Attribution

Code-OSS is sourced from Microsoft VS Code under the MIT license. Aiko keeps
the upstream LICENSE, notices, and third-party attribution files in every
distribution. Aiko's overlay and extension are MIT-licensed under this repo.
