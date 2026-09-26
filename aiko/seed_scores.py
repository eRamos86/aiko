"""One-time seed scores from public benchmarks (ADR-019).

These are SEEDS, not the truth: cold-start quality signal until the user's
own feedback ledger (aiko/feedback.py) accumulates enough outcomes to make
ranking behavioral. Seeded once, hand-maintained, never fetched at runtime
(no network dependency, no API costs, no stale-scrape surprises).

Format: {model_id_substring: {persona: 0..1 additive-on-default}}.
Match is case-insensitive substring on the model id — first rule wins,
so order matters: more specific ids first.
"""
# Sources: public evals as of Sep 2026 — SWE-bench (code/debug),
# GPQA/MMLU (research), GSM8K/MATH (data), MT-Bench/Arena Elo (chat),
# general instruction-following (teach/write). Translated to personas.
BENCH_SEEDS: list[tuple[str, dict]] = [
    ("nemotron-3-super",     {"code": .78, "debug": .74, "research": .80, "plan": .82,
                              "write": .75, "data": .72, "support": .70, "chat": .76,
                              "teach": .74, "devops": .72}),
    ("nemotron-3-ultra",     {"code": .85, "debug": .82, "research": .90, "plan": .92,
                              "write": .83, "data": .84, "support": .78, "chat": .82,
                              "teach": .85, "devops": .80}),
    ("nemotron-3-nano",      {"code": .62, "debug": .60, "research": .64, "plan": .66,
                              "write": .62, "data": .60, "support": .58, "chat": .65,
                              "teach": .62, "devops": .60}),
    ("deepseek-r1",          {"code": .72, "debug": .76, "research": .86, "plan": .88,
                              "write": .70, "data": .82, "support": .62, "chat": .68,
                              "teach": .76, "devops": .66}),
    ("qwen3-coder",          {"code": .90, "debug": .85, "research": .70, "plan": .72,
                              "write": .62, "data": .68, "support": .50, "chat": .60,
                              "teach": .66, "devops": .74}),
    ("qwen3.6",              {"code": .80, "debug": .76, "research": .78, "plan": .80,
                              "write": .72, "data": .74, "support": .66, "chat": .72,
                              "teach": .74, "devops": .70}),
    ("llama-3.3-70b",        {"code": .74, "debug": .70, "research": .80, "plan": .82,
                              "write": .78, "data": .74, "support": .72, "chat": .80,
                              "teach": .78, "devops": .66}),
    ("mistral-large",        {"code": .72, "debug": .68, "research": .78, "plan": .80,
                              "write": .82, "data": .70, "support": .72, "chat": .76,
                              "teach": .78, "devops": .64}),
    ("devstral",             {"code": .84, "debug": .80, "research": .64, "plan": .66,
                              "write": .58, "data": .62, "support": .48, "chat": .56,
                              "teach": .62, "devops": .72}),
    ("glm",                  {"code": .74, "debug": .72, "research": .76, "plan": .78,
                              "write": .74, "data": .72, "support": .70, "chat": .72,
                              "teach": .74, "devops": .70}),
    ("command-a",            {"code": .68, "debug": .64, "research": .74, "plan": .76,
                              "write": .80, "data": .68, "support": .74, "chat": .78,
                              "teach": .76, "devops": .60}),
    ("gpt-oss",              {"code": .76, "debug": .72, "research": .78, "plan": .80,
                              "write": .76, "data": .74, "support": .74, "chat": .78,
                              "teach": .78, "devops": .68}),
    # local-ollama scale: installed models score on their public reputation
    # but discounted vs premier hosted peers (hardware reality + smaller ctx)
    ("qwen3-coder:30b",      {"code": .80, "debug": .74, "research": .62, "plan": .64,
                              "write": .56, "data": .60, "support": .45, "chat": .54,
                              "teach": .58, "devops": .68}),
    ("deepseek-r1:32b",      {"code": .64, "debug": .68, "research": .78, "plan": .80,
                              "write": .62, "data": .74, "support": .55, "chat": .60,
                              "teach": .68, "devops": .58}),
    ("qwen3.6:35b",          {"code": .72, "debug": .68, "research": .70, "plan": .72,
                              "write": .64, "data": .66, "support": .58, "chat": .64,
                              "teach": .66, "devops": .62}),
    ("gpt-oss:20b",          {"code": .68, "debug": .64, "research": .66, "plan": .70,
                              "write": .66, "data": .64, "support": .64, "chat": .72,
                              "teach": .68, "devops": .60}),
]


def seed_scores(model_id: str) -> dict:
    """Benchmark seed vector for a model id, or empty dict if unseeded."""
    lid = model_id.lower()
    for sub, scores in BENCH_SEEDS:
        if sub.lower() in lid:
            return scores
    return {}
