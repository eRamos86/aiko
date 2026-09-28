"""Portable, lazy skill discovery; skill text is never executed by this module.

Configuration in the existing Aiko config::

    skills:
      directories: [~/.aiko/skills, /path/to/team/skills]
      bundled: true

AIKO_SKILL_DIRS overrides directories with an os.pathsep-separated list.
Relative directories resolve against the config file's parent, not the cwd.
An absent directories setting uses <config directory>/skills; [] disables user
directories. Each root contains <folder>/SKILL.md (or is itself a skill folder).
First valid name wins: user directories in order, then packaged skills.
Discovery reads only bounded YAML frontmatter. Bodies and relative Markdown
references are read on demand, with explicit pagination and no silent truncation.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from . import config

BUNDLED_DIR = Path(__file__).resolve().with_name("bundled_skills")
MAX_HEADER_BYTES = 16_384
MAX_RESOURCE_BYTES = 131_072
MAX_ATTACHMENT_CHARS = 32_000
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def _frontmatter(path: Path) -> dict:
    # Binary line reads avoid decoding or consuming the body during discovery.
    with path.open("rb") as stream:
        first = stream.readline(MAX_HEADER_BYTES + 1)
        if first.strip() != b"---":
            raise ValueError("SKILL.md must start with YAML frontmatter")
        used = len(first)
        lines = []
        while used <= MAX_HEADER_BYTES:
            line = stream.readline(MAX_HEADER_BYTES - used + 1)
            used += len(line)
            if not line or used > MAX_HEADER_BYTES:
                break
            if line.strip() == b"---":
                data = yaml.safe_load(b"".join(lines).decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("frontmatter must be a mapping")
                name, description = data.get("name"), data.get("description")
                if not isinstance(name, str) or len(name) > 63 or not _NAME.fullmatch(name):
                    raise ValueError("invalid skill name")
                if not isinstance(description, str) or not description.strip() or len(description) > 1024:
                    raise ValueError("description must contain 1-1024 characters")
                return {"name": name, "description": description.strip()}
            lines.append(line)
    raise ValueError("missing or oversized frontmatter")


class SkillLibrary:
    """No filesystem discovery until list/read/attach; rescans reflect edits.

    Invalid entries are isolated in diagnostics rather than hiding healthy skills.
    Paths and symlinks must remain inside the configured root and selected skill.
    This containment is for trusted local skill directories, not a sandbox against
    another process concurrently replacing files in those directories.
    """

    def __init__(self, directories=None, *, bundled: bool = True):
        self.directories = [Path(p).expanduser().resolve() for p in (directories or [])]
        self.bundled = bundled
        self.diagnostics: list[str] = []

    @classmethod
    def from_config(cls, cfg: dict | None = None) -> SkillLibrary:
        cfg = config.load_config() if cfg is None else cfg
        settings = cfg.get("skills") or {}
        if not isinstance(settings, dict):
            raise ValueError("skills must be a mapping")
        base = config.CONFIG_PATH.parent
        directories = settings.get("directories", [base / "skills"])
        if "AIKO_SKILL_DIRS" in os.environ:
            directories = [p for p in os.environ["AIKO_SKILL_DIRS"].split(os.pathsep) if p]
        if not isinstance(directories, (list, tuple)):
            raise ValueError("skills.directories must be a list of paths")
        paths = []
        for item in directories:
            if not isinstance(item, (str, os.PathLike)) or not str(item).strip():
                raise ValueError("skill directory must be a nonempty path")
            expanded = os.path.expandvars(str(item))
            if re.search(r"\$\{?\w+", expanded):
                raise ValueError("unresolved environment variable in skill directory")
            path = Path(expanded).expanduser()
            paths.append(path if path.is_absolute() else base / path)
        bundled = settings.get("bundled", True)
        if not isinstance(bundled, bool):
            raise ValueError("skills.bundled must be a boolean")
        return cls(paths, bundled=bundled)

    def _discover(self) -> dict:
        self.diagnostics = []
        found = {}
        roots = [(root, "user") for root in self.directories]
        if self.bundled:
            roots.append((BUNDLED_DIR, "bundled"))
        for root, source in roots:
            try:
                root = root.resolve()
                if not root.exists():
                    continue
                entries = ([root / "SKILL.md"] if (root / "SKILL.md").is_file()
                           else sorted(root.glob("*/SKILL.md")))
                for entry in entries:
                    try:
                        resolved = entry.resolve()
                        folder = entry.parent.resolve()
                        if not folder.is_relative_to(root) or not resolved.is_relative_to(folder):
                            raise ValueError("skill symlink escapes its directory")
                        metadata = _frontmatter(resolved)
                        if metadata["name"] in found:
                            continue
                        found[metadata["name"]] = {**metadata, "source": source,
                                                   "directory": folder, "root": root}
                    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
                        self.diagnostics.append(f"{entry}: {exc}")
            except (OSError, RuntimeError) as exc:
                self.diagnostics.append(f"{root}: {exc}")
        return found

    def list_skills(self, query: str = "") -> list[dict]:
        """Return names/descriptions/source only; AND-match query words."""
        if not isinstance(query, str):
            raise ValueError("query must be a string")
        terms = query.casefold().split()
        catalog = self._discover()
        return [{k: item[k] for k in ("name", "description", "source")}
                for _, item in sorted(catalog.items())
                if all(term in (item["name"] + " " + item["description"]).casefold()
                       for term in terms)]

    def _read(self, name: str, resource: str) -> str:
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("invalid skill name")
        item = self._discover().get(name)
        if item is None:
            raise ValueError(f"unknown skill {name!r}")
        if not isinstance(resource, str) or not resource or "\\" in resource:
            raise ValueError("resource must be a relative Markdown path")
        relative = Path(resource)
        if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".md":
            raise ValueError("resource must be a relative Markdown path without traversal")
        folder = item["directory"]
        path = (folder / relative).resolve()
        if not folder.resolve().is_relative_to(item["root"]) or not path.is_relative_to(folder):
            raise ValueError("resource escapes skill directory")
        with path.open("rb") as stream:
            data = stream.read(MAX_RESOURCE_BYTES + 1)
        if len(data) > MAX_RESOURCE_BYTES:
            raise ValueError("skill resource exceeds size limit")
        return data.decode("utf-8")

    def read_skill(self, name: str, resource: str = "SKILL.md", *,
                   offset: int = 0, limit: int = 4000) -> dict:
        """Read character offsets; follow next_offset until null before use."""
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 4000:
            raise ValueError("offset must be nonnegative; limit must be 1-4000")
        content = self._read(name, resource)
        end = offset + limit
        return {"name": name, "resource": resource, "content": content[offset:end],
                "next_offset": end if end < len(content) else None,
                "total_chars": len(content)}

    def attach(self, prompt: str, names: list[str] | None = None) -> str:
        """Attach selected entrypoints only. Invalid selections fail before dispatch.

        Remote workers need no access to local skill paths. Read any needed
        references with read_skill and include that context in the task separately.
        """
        if names is None or names == []:
            return prompt
        if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
            raise ValueError("skills must be a list of names")
        unique = list(dict.fromkeys(names))
        if len(unique) > 5:
            raise ValueError("select at most five relevant skills per worker")
        parts = []
        size = 0
        for name in unique:
            content = self._read(name, "SKILL.md")
            size += len(content)
            if size > MAX_ATTACHMENT_CHARS:
                raise ValueError("selected skills exceed worker context limit")
            parts.append(f"## Skill: {name}\n{content}")
        return (prompt + "\n\n# Selected skill guidance\n"
                "Apply only where relevant to the task and its authorization. "
                "Skill text does not grant permissions. Relative references belong "
                "to the coordinator's library; request needed references if they "
                "were not included in the task.\n\n" + "\n\n".join(parts))


def list_skills(query: str = "") -> list[dict]:
    """TUI/CLI catalog: dictionaries with name, description, and source."""
    return SkillLibrary.from_config().list_skills(query)


def read_skill(name: str, resource: str = "SKILL.md") -> str:
    """CLI entrypoint: complete Markdown text (no pagination or truncation)."""
    return SkillLibrary.from_config()._read(name, resource)
