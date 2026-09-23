import json
from pathlib import Path

from aikod.routing_log import write_decision, LOG_PATH
from aikod.router import RoutingDecision


def test_write_decision_appends(tmp_path, monkeypatch):
    monkeypatch.setattr("aikod.routing_log.LOG_PATH", tmp_path / "routing.log")
    d = RoutingDecision(
        provider_id="hermes", model="glm-5.2", yaml_score=0.7,
        observed_modifier=0.1, final_score=0.8,
        reasoning="task=plan. Top: hermes/glm-5.2",
        candidates=[{"provider": "hermes", "model": "glm-5.2", "final": 0.8}],
    )
    write_decision("t1", d)
    write_decision("t2", d)
    lines = (tmp_path / "routing.log").read_text().strip().split("\n")
    assert len(lines) == 2
    entry = json.loads(lines[0])
    assert entry["task_id"] == "t1"
    assert entry["chosen"] == {"provider": "hermes", "model": "glm-5.2"}
    assert entry["final_score"] == 0.8
