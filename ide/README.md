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

## Attribution

Code-OSS is sourced from Microsoft VS Code under the MIT license. Aiko keeps
the upstream LICENSE, notices, and third-party attribution files in every
distribution. Aiko's overlay and extension are MIT-licensed under this repo.
