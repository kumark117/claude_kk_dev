#!/usr/bin/env python3
"""Token usage per build attempt, read from logs/project_*.log (read-only).

usage: ./usage.py [LOGDIR]
Prints Markdown tables. One attempt can log several `result` lines: each line's
turns and tokens cover only its own segment, so they are added up, while cost
(and `model_usage`) are running totals, so the last one counts. An attempt with
no `result` line (still running, or killed) has no usage and is listed as such.
"""
import json
import re
import sys
from pathlib import Path

ATTEMPT = re.compile(r"^===== attempt (\d+) start ")
# Anchored on the timestamp, so tool output (indented) can never match.
EVENT = re.compile(r"^\d{4}-\d\d-\d\dT\S+ \| \+[\d:]+ \| (result|model_usage|model) (.*)$")
RESULT = re.compile(r"^(\S+) turns=(\S+) cost_usd=(\S+) usage=(\{.*\}|null)$")
KEYS = ("input_tokens", "cache_creation_input_tokens",
        "cache_read_input_tokens", "output_tokens")
MODEL_KEYS = ("inputTokens", "cacheCreationInputTokens",
              "cacheReadInputTokens", "outputTokens")


def parse(path):
    """Return {attempt: record} for one project log."""
    attempts, a = {}, None
    with open(path, errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if m := ATTEMPT.match(line):
                a = attempts[int(m[1])] = {"model": None, "subtype": None, "turns": 0,
                                           "tokens": [0] * len(KEYS), "cost": None,
                                           "by_model": {}}
            elif a is not None and (m := EVENT.match(line)):
                kind, rest = m[1], m[2]
                if kind == "model":
                    a["model"] = rest
                elif kind == "model_usage":
                    a["by_model"] = json.loads(rest)
                elif r := RESULT.match(rest):
                    usage = json.loads(r[4]) or {}
                    a["subtype"] = r[1]
                    a["turns"] += int(r[2]) if r[2].isdigit() else 0
                    a["tokens"] = [t + (usage.get(k) or 0) for t, k in zip(a["tokens"], KEYS)]
                    if r[3] != "None":
                        a["cost"] = float(r[3])
    return attempts


def row(*cells):
    print("| " + " | ".join(str(c) for c in cells) + " |")


def main():
    logs = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "logs")
    files = sorted(logs.glob("project_*.log"), key=lambda p: int(p.stem.split("_")[1]))
    head = ("Input", "Cache write", "Cache read", "Output", "Cost USD")
    row("Project", "Attempt", "Model", "Result", "Turns", *head)
    print("|---" * 10 + "|")
    total, total_cost, counted, missing, models = [0] * len(KEYS), 0.0, 0, 0, {}
    for path in files:
        for n, a in sorted(parse(path).items()):
            model = a["model"] or "not logged"
            if a["subtype"] is None:
                missing += 1
                row(path.stem, n, model, "no result", *["-"] * 6)
                continue
            counted += 1
            total = [x + y for x, y in zip(total, a["tokens"])]
            total_cost += a["cost"] or 0.0
            row(path.stem, n, model, a["subtype"], a["turns"],
                *(f"{t:,}" for t in a["tokens"]),
                "-" if a["cost"] is None else f"{a['cost']:.4f}")
            for name, u in a["by_model"].items():
                s = models.setdefault(name, [0, 0, 0, 0, 0.0])
                for i, k in enumerate(MODEL_KEYS + ("costUSD",)):
                    s[i] += u.get(k) or 0
    row("**Total**", counted, "", "", "", *(f"{t:,}" for t in total), f"{total_cost:.4f}")
    if missing:
        print(f"\n{missing} attempt(s) have no usage recorded, so the total excludes them.")
    if models:
        print("\n### By model (attempts that logged it)\n")
        row("Model", *head)
        print("|---" * 6 + "|")
        for name, s in sorted(models.items()):
            row(name, *(f"{t:,}" for t in s[:4]), f"{s[4]:.4f}")
    print("\nCost is Claude Code's own estimate. Interactive sessions are not included.")


if __name__ == "__main__":
    main()
