"""Docs auditor checks (deterministic tier)."""
import re

from aikod.docs_auditor import check_doc, _parse_frontmatter


GOOD = """---
type: hub
status: active
updatedAt: 2026-09-20
---

# Thing

Links: [[Other Doc]] and [[Another]].
"""


def test_parse_frontmatter():
    fm, body = _parse_frontmatter(GOOD)
    assert fm["type"] == "hub"
    assert fm["status"] == "active"


def test_good_doc_has_minimal_findings():
    findings = check_doc("Platform/X/X.md", GOOD)
    assert all(f["check"] != "frontmatter" or f["severity"] == "low"
               for f in findings)


def test_missing_frontmatter_flagged():
    findings = check_doc("X.md", "# just a heading\n")
    assert any(f["check"] == "frontmatter" for f in findings)


def test_staleness_flagged():
    old = GOOD.replace("2026-09-20", "2026-01-01")
    findings = check_doc("X.md", old)
    assert any(f["check"] == "staleness" and f["severity"] == "high"
               for f in findings)


def test_truncation_marker_flagged():
    bad = GOOD + "\nsome text ...[truncated]\n"
    findings = check_doc("X.md", bad)
    assert any(f["check"] == "integrity" for f in findings)


def test_scope_excludes_timeline():
    from aikod.docs_auditor import SCOPES
    assert "Timeline" not in SCOPES
    for s in ("Platform", "Agents", "Knowledge", "Resources", "Assets"):
        assert s in SCOPES
