#!/usr/bin/env bash
# Reset project_1..project_10, copy project_0's specs into them and rebuild all ten.
# Usage: ./run.sh PAR        e.g. ./run.sh 3
# DESTRUCTIVE: everything in those folders is deleted (old code stays in git history).
# Also clears the npm cache. project_0 is not touched. If a run is interrupted, resume with
# `PAR=N ./orchestrate.sh`; running this script again would start over.
[ -s "$HOME/.nvm/nvm.sh" ] && . "$HOME/.nvm/nvm.sh"
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOWNLOADS="${DOWNLOADS:-/mnt/c/Users/LENOVO/Downloads}"
STATE="$ROOT/.state"
PROJ=(project_{1..10})
die() { echo "run.sh: $*" >&2; exit 1; }

# ---------- checks: nothing is changed until all of these pass ----------
[ $# -eq 1 ] && [[ $1 =~ ^[1-9][0-9]*$ ]] || die "usage: ./run.sh PAR   (PAR = parallel builds, e.g. 3)"
par=$1
cd "$ROOT"
pgrep -f '^([^ ]*/)?bash ([^ ]*/)?orchestrate\.sh( run)?$' >/dev/null \
  && die "an orchestrator is already running"
command -v "${CLAUDE_BIN:-claude}" >/dev/null || die "claude not found"
command -v npm >/dev/null || die "npm not found"
for f in project_0/SPEC.md project_0/requirements.md; do
  [ -s "$f" ] || die "$f is missing or empty"
done
dirty=$(git status --porcelain --untracked-files=no) || die "git status failed"
new=$(git status --porcelain -- "${PROJ[@]}") || die "git status failed"
[ -z "$dirty$new" ] || die "uncommitted changes; commit or discard them first:
$(git status --short)"

echo "This DELETES everything in project_1..project_10 (code, node_modules, any .env),"
echo "clears the npm cache, rebuilds all ten with PAR=$par (uses Claude usage),"
echo "and pushes to GitHub."
read -r -p "Type yes to continue: " ans || ans=
[ "$ans" = yes ] || die "cancelled; nothing changed"

npm cache clean --force || die "could not clear the npm cache; projects not reset"

# ---------- keep the old results ----------
arch="logs/archive/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$arch" "$STATE"
for f in logs/orchestrator.log "${PROJ[@]/#/logs/}"; do
  [[ $f == *.log ]] || f="$f.log"
  [ ! -f "$f" ] || mv "$f" "$arch/"
done
[ ! -f REPORT.md ] || cp REPORT.md "$arch/"
if [ -f all_timings.log ]; then
  cp all_timings.log "$arch/"
  # keep only project_0's history, so the new report describes this run
  awk -F' \\| ' '$2 == "project_0"' "$arch/all_timings.log" > all_timings.log
fi

# ---------- reset and copy specs ----------
rm -f "$STATE/limit_until"
for p in "${PROJ[@]}"; do
  mkdir -p "$p"
  find "$ROOT/$p" -mindepth 1 ! -path "$ROOT/$p/requirements.md" -delete
  rm -f "$STATE/$p".*
  for f in requirements.md SPEC.md; do
    sed "1s/project_0/$p/" "project_0/$f" > "$p/$f"
  done
done
echo "Reset done; old results are in $arch. Starting the builds."

# ---------- build ----------
ORCH_ROOT="$ROOT" PROJECTS=auto PAR="$par" ./orchestrate.sh run < /dev/null

# the Downloads copy gets a preface and per-step timings ahead of the report
prefaced_report() {  # $1 = destination
  python3 -I - "$1" "$par" <<'PY'
import collections, sys
dest, par = sys.argv[1:]
report = open("REPORT.md").read().split("\n", 1)[1]
t = collections.defaultdict(collections.Counter)
for line in open("all_timings.log"):
    f = line.rstrip("\n").split(" | ")
    if len(f) != 5 or not f[1].startswith("project_"):
        continue
    _, p, phase, event, secs = f
    secs = float(secs)
    if event.startswith("tool_"):
        t[p]["tool"] += secs
        t[p]["calls"] += 1
    elif event.startswith("attempt_") and "_end_" in event:
        t[p]["build"] += secs
    elif phase == "test" and event.startswith("run_rc"):
        t[p]["test"] += secs
    elif event == "passed":
        t[p]["passed"] = secs
rows = ["| Project | Claude build | of which tool calls (count) | Orchestrator test run | First attempt to pass |",
        "|---|---|---|---|---|"]
for p in sorted(t, key=lambda p: int(p.split("_")[1])):
    v = t[p]
    total = f"{v['passed']:.0f}s" if "passed" in v else "not passed"
    rows.append(f"| {p} | {v['build']:.0f}s | {v['tool']:.0f}s ({v['calls']}) | {v['test']:.0f}s | {total} |")
preface = f"""# Pipeline report

## About this project-of-projects

One orchestrator (`orchestrate.sh`) builds many small projects with headless Claude Code. Each
`project_N` is the same very minimal RAG app: a Next.js page sends a question to a FastAPI server,
which retrieves text from local files and passes it to an LLM (a built-in stub by default, with an
optional OpenAI-compatible proxy). `project_0` was designed first and its `SPEC.md` reused for
`project_1` to `project_10`. For each build, the orchestrator re-runs the tests, then commits and pushes.

## Reading these numbers

- `project_1` to `project_10` were reset and rebuilt with `PAR={par}`, after clearing the npm cache.
- `project_0` was not rebuilt: its figures come from its earlier run.
- Design time covers only approving an existing `SPEC.md`, not an interactive design session.
- Tokens are recorded per build attempt, not per step. Design and test steps have no token figures.
- Build and test times add up all attempts.
"""
steps = "\n".join(rows)
open(dest, "w").write(f"{preface}\n## Timings by step\n\n{steps}\n\n## Run summary\n{report}")
PY
}

if [ -d "$DOWNLOADS" ]; then
  prefaced_report "$DOWNLOADS/REPORT.md" && echo "Copied REPORT.md to $DOWNLOADS"
else
  echo "run.sh: $DOWNLOADS not found; REPORT.md not copied" >&2
fi

failed=()
for p in "${PROJ[@]}"; do [ -f "$STATE/$p.build.done" ] || failed+=("$p"); done
[ ${#failed[@]} -eq 0 ] || die "not passed: ${failed[*]}"
echo "All ten builds passed."
