#!/usr/bin/env python3
"""Turn `claude -p --output-format stream-json` into a readable, timed log.

usage: logstream.py PROJECT ERRFILE TIMINGS...
stdout: one line per event with elapsed time; tool calls also get their duration.
ERRFILE: only error results and non-JSON output (used to detect usage limits).
"""
import json
import sys
import time
from datetime import datetime

project, errfile, timings = sys.argv[1], sys.argv[2], sys.argv[3:]
start = time.monotonic()
pending = {}  # tool_use id -> (name, summary, start)


def stamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clock(sec):
    return "%02d:%02d" % divmod(int(sec), 60)


def log(msg):
    print(f"{stamp()} | +{clock(time.monotonic() - start)} | {msg}", flush=True)


def error(msg):
    with open(errfile, "a") as f:
        f.write(msg + "\n")


for raw in sys.stdin:
    line = raw.rstrip("\n")
    try:
        ev = json.loads(line)
        kind = ev.get("type")
    except (ValueError, AttributeError):
        log(line)
        error(line)
        continue
    if kind in ("assistant", "user"):
        content = ev.get("message", {}).get("content", [])
        for c in content if isinstance(content, list) else []:
            if c.get("type") == "text" and kind == "assistant":
                log("claude: " + c["text"])
            elif c.get("type") == "tool_use":
                arg = c.get("input", {})
                what = str(arg.get("command") or arg.get("file_path") or "")
                pending[c["id"]] = (c["name"], what, time.monotonic())
                log(f"start {c['name']}: {what}")
            elif c.get("type") == "tool_result" and c.get("tool_use_id") in pending:
                name, what, t0 = pending.pop(c["tool_use_id"])
                dur = time.monotonic() - t0
                state = "ERROR" if c.get("is_error") else "ok"
                log(f"done  {name} ({dur:.1f}s, {state}): {what[:120]}")
                out = c.get("content")
                if isinstance(out, list):
                    out = "\n".join(str(x.get("text", "")) for x in out)
                if out:
                    print("    " + str(out)[-2000:].replace("\n", "\n    "), flush=True)
                for path in timings:
                    with open(path, "a") as f:
                        f.write(f"{stamp()} | {project} | build | tool_{name} | {dur:.1f}\n")
    elif kind == "result":
        log(f"result {ev.get('subtype')} turns={ev.get('num_turns')} "
            f"cost_usd={ev.get('total_cost_usd')} usage={json.dumps(ev.get('usage'))}")
        if ev.get("is_error"):
            error(f"{ev.get('subtype')} {ev.get('result', '')}")
