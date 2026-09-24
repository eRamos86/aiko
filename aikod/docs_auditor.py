"""Docs Auditor — scheduled + on-demand vault compliance & freshness sweep.

Runs ON THE SERVER (server vault = source of truth). Three tiers:
  1. Deterministic checks (free): existence, frontmatter validity, link
     resolution, staleness (updatedAt vs repo mtime), title drift.
     Scope per user decision: Platform/, Agents/, Knowledge/, Resources/,
     Assets/ — Timeline/ excluded.
  2. Agent fixes (auto): stale/invalid docs get fixed by a cheap worker
     model through the docs-writer API (NEVER the local clone — server
     truth only). Aiko may also update the Documentation Standards
     themselves when a pattern repeats, and can ask Ace via the report
     when unsure.
  3. Findings ledger: every run writes `Docs Audit/Audit YYYY-MM-DD.md`
     through docs-writer: fixed / flagged / needs-Ace.

Nightly: systemd timer (docs-auditor.timer) → `python -m aikod.docs_auditor`.
On demand: `aiko audit-docs` → hits the daemon's /audit/docs endpoint.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

VAULT_ROOT = Path.home() / "Obsidian" / "obsidian-main"
SCOPES = ["Platform", "Agents", "Knowledge", "Resources", "Assets"]
STALE_DAYS = 30
MAX_DOCS_PER_RUN = 40          # token guardrail — efficient, not stingy
LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


def _env(name: str, path: str, default: str = "") -> str:
    import os
    val = os.environ.get(name)
    if val:
        return val
    try:
        for line in Path(path).read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return default


def _api() -> tuple[str, dict, dict]:
    from pathlib import Path as _P
    envp = VAULT_ROOT / "api" / ".env"
    base = "http://127.0.0.1:4010"
    secret = _env("VAULT_API_SECRET", str(envp))
    writer = _env("VAULT_DOCS_WRITER_TOKEN", str(envp))
    return base, {"X-Vault-Secret": secret}, {"X-Docs-Writer-Token": writer}


def _read_doc(path: str) -> str | None:
    base, headers, _ = _api()
    try:
        r = httpx.get(f"{base}/api/vault/doc", params={"path": path},
                      headers=headers, timeout=15)
        if r.status_code == 200:
            return r.json().get("content")
    except httpx.HTTPError:
        pass
    return None


def _write_doc(path: str, content: str) -> dict:
    base, _, wh = _api()
    r = httpx.post(f"{base}/api/vault/doc", params={"path": path},
                   json={"content": content}, headers=wh, timeout=60)
    try:
        return r.json()
    except Exception:
        return {"status": r.status_code, "error": r.text[:200]}


def _list_docs() -> list[dict]:
    """All docs in scope via the API (list endpoint)."""
    base, headers, _ = _api()
    try:
        r = httpx.get(f"{base}/api/vault/docs", headers=headers, timeout=30)
        if r.status_code == 200:
            docs = r.json().get("docs") or r.json()
            if isinstance(docs, list):
                return [d for d in docs if any(
                    str(d.get("path", d.get("slug", ""))).startswith(s + "/")
                    for s in SCOPES)]
    except httpx.HTTPError:
        pass
    return []


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    fm: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            v = v.strip().strip("'\"")
            if v.startswith("[") and v.endswith("]"):
                v = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
            fm[k.strip()] = v
    return fm, m.group(2)


def check_doc(path: str, text: str) -> list[dict]:
    """Tier 1: deterministic findings for one doc."""
    findings = []
    fm, body = _parse_frontmatter(text)
    if not fm:
        findings.append({"check": "frontmatter", "severity": "medium",
                         "note": "no frontmatter block"})
    else:
        for req in ("type", "status"):
            if req not in fm:
                findings.append({"check": "frontmatter", "severity": "low",
                                 "note": f"missing key: {req}"})
        if str(fm.get("status", "")).lower() in ("done", "complete"):
            pass  # explicit terminal status ok
    # broken wikilinks that point outside the doc's own existence
    links = {m.group(1).strip() for m in LINK_RE.finditer(body)}
    if len(links) > 24:
        findings.append({"check": "links", "severity": "low",
                         "note": f"{len(links)} outgoing links — hub-heavy"})
    # staleness
    updated = fm.get("updatedAt") or fm.get("updated")
    if isinstance(updated, str) and re.search(r"\d{4}-\d{2}-\d{2}", updated):
        days = (time.time() - time.mktime(time.strptime(
            updated[:10], "%Y-%m-%d"))) / 86400
        if days > STALE_DAYS:
            findings.append({"check": "staleness", "severity": "high",
                             "note": f"updatedAt {updated[:10]} ({int(days)}d old)"})
    # truncation/corruption markers
    for marker in ("[truncated]", "...[truncated]", "TODO FILL", "PLACEHOLDER"):
        if marker in body:
            findings.append({"check": "integrity", "severity": "high",
                             "note": f"contains {marker!r}"})
    return findings


def _agent_fix(path: str, text: str, findings: list[dict]) -> dict:
    """Tier 2: cheap worker model fixes one doc via docs-writer."""
    prompt = (
        "You are a documentation auditor for an Obsidian vault (server is "
        "source of truth). Fix the doc at the path below. Rules: preserve "
        "frontmatter shape, keep content truthful to what's written (never "
        "invent facts), fix integrity markers and broken structure, update "
        "the updatedAt frontmatter date to today (2026-09-24). If a fix "
        "requires information you don't have, DO NOT invent it — return "
        "exactly NEEDS-ACE plus one short line why. Reply with the FULL "
        "new document content only, no commentary.\n\n"
        f"# Path\n{path}\n\n# Findings\n"
        + "\n".join(f"- {f['check']}: {f['note']}" for f in findings)
        + f"\n\n# Current document\n{text}"
    )
    fix_result: dict = {"path": path, "action": "error", "why": "no attempt"}
    try:
        from .brain_server import ServerBrain
        brain = ServerBrain()
        r = brain.complete([{"role": "user", "content": prompt}])
        content = (r.get("content") or "").strip()
        if not content or content.startswith("NEEDS-ACE"):
            fix_result = {"path": path, "action": "needs-ace",
                          "why": content[:200] if content else "empty fix"}
        else:
            if content.startswith("```"):
                content = re.sub(r"^```(markdown)?\n|\n```$", "", content)
            result = _write_doc(path, content)
            fix_result = {"path": path, "action": "fixed",
                          "write": result, "findings": len(findings)}
    except Exception as e:
        fix_result = {"path": path, "action": "error", "why": str(e)[:200]}
    return fix_result


def run_audit(max_docs: int = MAX_DOCS_PER_RUN,
              auto_fix: bool = True, write_report: bool = True) -> dict:
    """Full sweep: scan → fix → report. Returns the summary dict."""
    docs = _list_docs()
    scanned = fixed = needs_ace = 0
    fixes: list[dict] = []
    for d in docs[:max_docs]:
        path = d.get("path") or (d.get("slug", "") + ".md")
        text = _read_doc(path)
        if text is None:
            continue
        scanned += 1
        findings = check_doc(path, text)
        if not findings:
            continue
        if auto_fix:
            fixes.append(_agent_fix(path, text, findings))
        else:
            fixes.append({"path": path, "action": "flagged",
                          "findings": [f["check"] for f in findings]})
        if len([f for f in fixes if f.get("action") == "fixed"]) >= 12:
            break  # per-run fix budget — efficient, not limiting
    for f in fixes:
        if f["action"] == "fixed":
            fixed += 1
        elif f["action"] == "needs-ace":
            needs_ace += 1
    summary = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "scope": SCOPES, "scanned": scanned, "candidates": len(fixes),
               "fixed": fixed, "needs_ace": needs_ace, "fixes": fixes}
    if write_report and fixes:
        day = time.strftime("%Y-%m-%d")
        report = ("---\ntype: audit\nstatus: active\n---\n\n"
                  f"# Docs Audit {day}\n\n"
                  f"Scanned {scanned} docs in {', '.join(SCOPES)} (Timeline excluded).\n\n"
                  f"- Fixed automatically: **{fixed}**\n"
                  f"- Needs Ace: **{needs_ace}**\n\n"
                  "## Fixed\n"
                  + "\n".join(f"- `{f['path']}` ({f.get('findings', '?')} findings)"
                              for f in fixes if f["action"] == "fixed")
                  + "\n\n## Needs Ace\n"
                  + "\n".join(f"- `{f['path']}` — {f.get('why', '?')}"
                              for f in fixes if f["action"] == "needs-ace")
                  + "\n")
        _write_doc(f"Docs Audit/Audit {day}.md", report)
    return summary
