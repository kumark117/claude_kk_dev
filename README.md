# claude_kk_dev

A pipeline that turns `project_{ix}/requirements.md` files into built, tested RAG projects with Claude Code.
Design is **interactive and happens once**, in a single session.
Builds run **headless and in parallel**, and each build starts as soon as its `SPEC.md` exists.

## Layout
| Path | What |
|---|---|
| `orchestrate.sh` | the orchestrator (in charge of everything) |
| `config.sh` | plan, PAR, models, turns, retries, waits, projects (env vars override) |
| `project_{ix}/` | `requirements.md` → `SPEC.md` → code, `test.sh`, README, `timings.log` |
| `all_timings.log` | `ISO timestamp \| project \| phase \| event \| duration_s` |
| `logs/project_{ix}.log` | full build output per project |
| `REPORT.md` | per-project results and totals, written at the end of a run |
| `.state/` | resume markers (local, not committed) |

## Usage
```bash
tmux new -s rag            # or: tmux attach -t rag
./orchestrate.sh dry-run   # full pipeline with a stub claude in .dryrun/ (no usage, no push)
./orchestrate.sh           # real run; Ctrl-C is safe: rerun to resume
./orchestrate.sh status    # per-project state
./orchestrate.sh report    # regenerate REPORT.md
```
To add a project, create `project_N/requirements.md` and rerun.

## How it works
1. **Design (once):** a single interactive Sonnet session covers every project that has no `SPEC.md`.
   Shared decisions are asked once. Approving a project writes `SPEC.md` and `.approved`, and its build starts.
   Projects that already have a `SPEC.md` are not designed again.
2. **Build:** up to `PAR` (default 3) headless build+test jobs run at once. Each works test-first, writes
   `test.sh` and the README, and doesn't run git itself.
3. **Pass check:** the orchestrator re-runs `test.sh`, commits under a lock, scans for secrets and pushes.
4. **Failures:** a usage limit means a 30-minute wait with concurrency held at `LIMIT_PAR=1`; any other
   failure means a 3-minute wait. Up to 6 retries; a waiting build frees its slot.
5. **Logging:** `logs/project_N.log` records every message and tool call with elapsed time and step duration
   (`logstream.py`); `logs/orchestrator.log` and `all_timings.log` record the elapsed time of running tasks
   every `HEARTBEAT_S` seconds. `./orchestrate.sh status` shows current elapsed times.

Note: build sessions get unrestricted Bash. Run this only on a disposable machine or VM.
