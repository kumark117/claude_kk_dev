# claude_kk_dev

A pipeline that turns `project_{ix}/requirements.md` files into built, tested RAG projects with Claude Code.
Design is **sequential and interactive**: you brainstorm one project at a time.
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
1. **Design:** for each project, in order, it opens `claude --model sonnet` in that project's folder with the
   superpowers brainstorming prompt. You answer one question at a time. On approval, Claude writes `SPEC.md`.
   You `/exit`, and the script commits and pushes the SPEC, then queues the build.
2. **Build:** a background scheduler runs up to `PAR` builds at once:
   `claude -p --model sonnet --max-turns N --permission-mode acceptEdits --tools Bash,Read,Write,Edit`.
   Each build works test-first, writes `test.sh` and the README, and doesn't run git itself.
3. **Pass check:** the orchestrator re-runs `test.sh` itself, then commits under a lock (all builds share
   one working tree), scans for secrets and pushes. A build passes only if the tests pass, a commit exists,
   the secret scan is clean and the push succeeded.
4. **Failures:** a usage limit means a 30-minute wait, with concurrency held at `LIMIT_PAR=1` until the
   wait ends. Any other failure means a 3-minute wait. Each build gets up to 6 retries, and a waiting
   build frees its slot.

Note: build sessions get unrestricted Bash. Run this only on a disposable machine or VM.
