"""Daemon-side outcome learning: fold results into router modifiers.

The router reads observed.json modifiers on every route (score = yaml
floor + observed modifier, clamped). This module is what WRITES them:
when a task finishes, its outcome (inferred from the orchestrator's
reply + task state) updates rolling per-(provider, model, task-type)
success stats, and once enough attempts accumulate, the modifier
drifts: proven combos get promoted (+up to 0.15), flaky ones demoted
(−down to 0.30). Self-contained in observed.json — the router needs
no changes; learning rides the file it already reads.
"""
from __future__ import annotations

import json
from pathlib import Path

MIN_ATTEMPTS = 3

FAILURE_MARKERS = (
    "hit the tool-round limit",
    "failed",
    "unable to complete",
    "could not complete",
    "no viable",
    "error:",
)


def learn_observed(db_path, task_id: str, reply: str,
                   observed_path=None, task_type: str | None = None) -> dict | None:
    """Record one task outcome into observed.json. Returns the learning delta."""
    from .db import connect
    from .router import OBSERVED_PATH, load_observed
    from .scheduler import infer_task_type

    p = Path(observed_path) if observed_path else OBSERVED_PATH
    conn = connect(Path(db_path))
    row = conn.execute(
        "SELECT assigned_provider, assigned_model, title, spec "
        "FROM task WHERE id=?", (task_id,)).fetchone()
    if not row or not row[0]:
        return None
    provider, model, title, spec = row
    shape = task_type or infer_task_type((title or "") + "\n" + (spec or ""))

    low = (reply or "").lower()
    failed = any(m in low for m in FAILURE_MARKERS)

    data = load_observed(p)
    prov = (data.setdefault("providers", {})
               .setdefault(provider, {})
               .setdefault("models", {})
               .setdefault(model, {}))
    st = prov.setdefault("stats", {}).setdefault(shape, {"wins": 0, "total": 0})
    st["total"] += 1
    if not failed:
        st["wins"] += 1

    out = {"provider": provider, "model": model, "shape": shape,
           "failed": failed, "total": st["total"]}
    if st["total"] >= MIN_ATTEMPTS:
        rate = st["wins"] / st["total"]
        mod = round(max(-0.30, min(0.15, (rate - 0.5) * 0.5)), 3)
        prov.setdefault("modifier", {})[shape] = mod
        out["modifier"] = mod
        out["rate"] = round(rate, 3)
    else:
        out["pending"] = MIN_ATTEMPTS - st["total"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))
    return out
