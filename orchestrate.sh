#!/usr/bin/env bash
# Two-phase pipeline: sequential interactive design -> parallel headless builds.
# Usage: ./orchestrate.sh [run|status|report|dry-run]
# load nvm so node/npm are the Linux ones, whatever shell started us
[ -s "$HOME/.nvm/nvm.sh" ] && . "$HOME/.nvm/nvm.sh"
set -uo pipefail

ROOT="${ORCH_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
# shellcheck source=config.sh
source "$ROOT/config.sh"
STATE="$ROOT/.state"; LOGS="$ROOT/logs"; TLOG="$ROOT/all_timings.log"
mkdir -p "$STATE" "$LOGS"

now()  { date +%s; }
clock() { printf '%02d:%02d:%02d' $(($1 / 3600)) $(($1 % 3600 / 60)) $(($1 % 60)); }
since() { echo $(( $(now) - $(cat "$1" 2>/dev/null || now) )); }
say()  { printf '[orch %s] %s\n' "$(date +%H:%M:%S)" "$*"; }

# ISO timestamp | project | phase | event | duration_s
tlog() {
  local line; line="$(date -Iseconds) | $1 | $2 | $3 | ${4:-0}"
  echo "$line" >> "$TLOG"
  [ -d "$ROOT/$1" ] && echo "$line" >> "$ROOT/$1/timings.log"
  return 0
}

projects() {
  if [ "$PROJECTS" = auto ]; then
    for d in "$ROOT"/project_*/requirements.md; do
      [ -f "$d" ] && basename "$(dirname "$d")"
    done | sort -t_ -k2 -n
  else
    for i in $PROJECTS; do echo "project_$i"; done
  fi
}

# ---------- git (serialised by a lock: builds share one working tree) ----------
secret_scan() {  # checks what is staged; returns 1 on a hit
  local bad
  bad=$(git diff --cached --name-only --diff-filter=ACMR \
        | grep -E '(^|/)(\.env(\..+)?|[^/]*\.(pem|key|p12)|id_rsa[^/]*|credentials[^/]*\.json)$' \
        | grep -vE '\.env\.example$')
  [ -n "$bad" ] && { say "SECRET FILE staged: $bad"; return 1; }
  if git diff --cached -U0 --diff-filter=ACMR | grep -E '^\+' | grep -qE \
     'sk-ant-[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9]{32,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}'; then
    say "SECRET-LIKE string in staged diff"; return 1
  fi
}

git_sync() {  # git_sync "message" path... ; commits those paths and pushes
  local msg=$1; shift
  (
    flock 9
    cd "$ROOT" || exit 1
    local paths=() x
    for x in "$@"; do [ -e "$x" ] || git ls-files --error-unmatch -- "$x" >/dev/null 2>&1 && paths+=("$x"); done
    [ ${#paths[@]} -gt 0 ] || exit 0
    git add -A -- "${paths[@]}" || exit 1
    if ! secret_scan; then git reset -q -- "${paths[@]}"; exit 2; fi
    git diff --cached --quiet || git commit -q -m "$msg" || exit 1
    for _ in 1 2 3; do
      git push -q "$GIT_REMOTE" "$GIT_BRANCH" 2>/dev/null && exit 0
      git pull -q --rebase --autostash "$GIT_REMOTE" "$GIT_BRANCH"
      sleep 3
    done
    exit 1
  ) 9>"$STATE/git.lock"
}

# ---------------------------------- prompts ----------------------------------
design_prompt() { cat <<EOF
Use the superpowers:brainstorming skill to design these projects, ALL in this one session: $*
Each has a requirements.md in its folder. Read them all first.
- Ask each shared decision ONCE and apply it to every project. Ask project-specific questions only where the requirements differ.
- Ask ONE question at a time, multiple-choice, with a recommended default I can accept with one word. Keep it minimal (YAGNI).
- Before choosing versions, check the installed toolchain (node -v, npm -v, python3 --version, etc.) and pin exact versions that work on this machine.
- When I approve a project's design, write <project>/SPEC.md (not the skill's usual docs/ spec file), and only after it is complete create an empty file <project>/.approved. Its build starts as soon as that file exists.
- Each SPEC.md must be self-contained: stack and exact versions; data flow; components and file layout; test plan (unit tests plus an offline end-to-end test using mocks/fixtures, with no network and no real LLM); acceptance criteria; exact test commands, all run by a single ./test.sh that the build must create; environment variable NAMES only (no values).
- Write nothing else. No code, no git, no planning or implementation. After the last SPEC.md, tell me to type /exit.
EOF
}

build_prompt() { cat <<EOF
Build the project in the current directory non-interactively. Never ask questions: make sensible choices and note them in README.md.
SPEC.md is the source of truth. Implement exactly what it says, with no extra features.
- Work only inside this directory. Do not run git (the orchestrator commits and pushes).
- TDD: write tests first, then the code, and iterate until every test passes.
- Create an executable ./test.sh: bash, non-interactive, offline (mocks/fixtures; no real LLM or network calls apart from installing dependencies), idempotent dependency setup (e.g. python3 -m venv .venv, npm install). It must run all unit and end-to-end tests and exit 0 only if all pass. Any server it starts runs in the background and is killed on exit.
- Write README.md with setup, usage and test instructions. Provide .env.example with variable names only. Never write real secrets.
- Make sure .gitignore covers .env, .venv, node_modules, .next, __pycache__ and caches.
- Other builds run in parallel on this machine: use free ports (never assume 3000 or 8000 are free) and never kill processes you did not start.
- Finish by running ./test.sh and confirming that it passes.
This is attempt $1. If files already exist from an earlier attempt, continue from them instead of starting over.
EOF
}

# --------------------------------- build phase --------------------------------
build_attempt() {  # runs in the background, records its outcome in .state
  local p=$1 dir="$ROOT/$1" out="$STATE/$1.attempt.out" n t0 rc dur trc=1 wait_s
  n=$(( $(cat "$STATE/$p.attempts" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$STATE/$p.attempts"
  [ -f "$STATE/$p.build.first" ] || now > "$STATE/$p.build.first"
  tlog "$p" build "attempt_${n}_start" 0
  t0=$(now)
  : > "$out"; echo "build $t0" > "$STATE/$p.task"
  echo "===== attempt $n start $(date -Iseconds) =====" >> "$LOGS/$p.log"
  ( cd "$dir" && timeout "$BUILD_TIMEOUT_S" "$CLAUDE_BIN" -p --model "$MODEL_BUILD" \
      --max-turns "$MAX_TURNS" --permission-mode acceptEdits \
      --tools "Bash,Read,Write,Edit" --allowedTools "Bash,Read,Write,Edit" \
      --disable-slash-commands --no-session-persistence \
      --output-format stream-json --verbose \
      "$(build_prompt "$n")" ) < /dev/null 2>&1 \
    | python3 "$ROOT/logstream.py" "$p" "$out" "$TLOG" "$dir/timings.log" >> "$LOGS/$p.log"
  rc=${PIPESTATUS[0]}; dur=$(( $(now) - t0 ))
  echo "===== attempt $n rc=$rc elapsed $(clock "$dur") =====" >> "$LOGS/$p.log"
  tlog "$p" build "attempt_${n}_end_rc$rc" "$dur"
  say "$p attempt $n: claude finished rc=$rc after $(clock "$dur")"

  if [ "$rc" -ne 0 ] && tail -n 30 "$out" | grep -qiE 'usage limit|limit reached|hit your limit|limit will reset|resets at|rate.?limit'; then
    wait_s=$WAIT_LIMIT_S; echo $(( $(now) + wait_s )) > "$STATE/limit_until"
    tlog "$p" build usage_limit 0
  else
    if [ -f "$dir/test.sh" ]; then        # the orchestrator re-runs the tests itself
      t0=$(now); echo "test $t0" > "$STATE/$p.task"; tlog "$p" test run_start 0
      ( cd "$dir" && timeout "$TEST_TIMEOUT_S" bash ./test.sh ) < /dev/null >> "$LOGS/$p.log" 2>&1
      trc=$?; tlog "$p" test "run_rc$trc" $(( $(now) - t0 ))
    fi
    echo "$trc" > "$STATE/$p.test.rc"
    if [ "$trc" -eq 0 ]; then
      if git_sync "$p: build (attempt $n)" "$p"; then
        # git_sync only succeeds once the push went through; also require a build commit
        if [ -n "$(cd "$ROOT" && git log --format=%H "$(cat "$STATE/$p.design.sha")"..HEAD -- "$p")" ]; then
          dur=$(( $(now) - $(cat "$STATE/$p.build.first") ))
          echo "$dur" > "$STATE/$p.build.secs"; touch "$STATE/$p.build.done"
          tlog "$p" build passed "$dur"; say "$p build PASSED, total $(clock "$dur")"
          rm -f "$STATE/$p.build.pid" "$STATE/$p.build.next" "$STATE/$p.task"; return 0
        fi
        tlog "$p" build no_commit_or_unpushed 0
      else
        tlog "$p" build git_sync_failed 0
      fi
    fi
    wait_s=$WAIT_FAIL_S
  fi

  if [ "$n" -gt "$MAX_RETRIES" ]; then
    touch "$STATE/$p.build.failed"; tlog "$p" build gave_up 0; say "$p build FAILED after $n attempts"
    git_sync "$p: build failed after $n attempts (partial)" "$p" >/dev/null 2>&1
  else
    echo $(( $(now) + wait_s )) > "$STATE/$p.build.next"
    tlog "$p" build retry_wait "$wait_s"; say "$p attempt $n failed; retrying in ${wait_s}s"
  fi
  rm -f "$STATE/$p.build.pid" "$STATE/$p.task"
}

approve() {  # approve project: commit SPEC.md and queue its build (safe to call twice)
  local p=$1 el
  (
    flock 8
    [ -f "$STATE/$p.design.done" ] && exit 0
    git_sync "$p: design (SPEC.md)" "$p" || exit 1
    (cd "$ROOT" && git rev-parse HEAD) > "$STATE/$p.design.sha"
    el=$(since "$STATE/design.start"); echo "$el" > "$STATE/$p.design.secs"
    touch "$STATE/$p.design.done"; tlog "$p" design approved "$el"
    say "$p SPEC.md approved after $(clock "$el"); build queued"
  ) 8>"$STATE/approve.lock"
}

heartbeat() {  # elapsed time of everything still in progress
  local p task t0 el
  if [ -f "$STATE/design.start" ] && [ ! -f "$STATE/designs.finished" ]; then
    el=$(since "$STATE/design.start"); tlog root design running "$el"; say "design session: $(clock "$el") elapsed"
  fi
  for p in $(projects); do
    [ -f "$STATE/$p.task" ] || continue
    read -r task t0 < "$STATE/$p.task" || continue
    el=$(( $(now) - t0 )); tlog "$p" "$task" running "$el"
    say "$p $task running: $(clock "$el") (since first attempt $(clock "$(since "$STATE/$p.build.first")"))"
  done
}

running_count() {
  local c=0 f
  for f in "$STATE"/*.build.pid; do
    [ -f "$f" ] || continue
    if kill -0 "$(cat "$f")" 2>/dev/null; then c=$((c + 1)); else rm -f "$f"; fi
  done
  echo "$c"
}

dispatcher() {  # background scheduler: starts builds up to the concurrency limit
  local p par running pending beat=0
  while :; do
    if [ $(( $(now) - beat )) -ge "$HEARTBEAT_S" ]; then heartbeat; beat=$(now); fi
    par=$PAR
    [ "$(cat "$STATE/limit_until" 2>/dev/null || echo 0)" -gt "$(now)" ] && par=$LIMIT_PAR
    running=$(running_count); pending=0
    for p in $(projects); do
      [ -f "$STATE/$p.design.done" ] || { [ -f "$ROOT/$p/.approved" ] && [ -s "$ROOT/$p/SPEC.md" ] && approve "$p"; }
      [ -f "$STATE/$p.design.done" ] || { [ -f "$STATE/designs.finished" ] || pending=1; continue; }
      [ -f "$STATE/$p.build.done" ] || [ -f "$STATE/$p.build.failed" ] && continue
      pending=1
      [ -f "$STATE/$p.build.pid" ] && continue
      [ "$(cat "$STATE/$p.build.next" 2>/dev/null || echo 0)" -gt "$(now)" ] && continue
      [ "$running" -ge "$par" ] && continue
      build_attempt "$p" & echo $! > "$STATE/$p.build.pid"
      running=$((running + 1)); say "$p build started (running=$running, par=$par)"
    done
    [ "$pending" -eq 0 ] && [ "$(running_count)" -eq 0 ] && break
    sleep "$POLL_S"
  done
}

# -------------------------------- design phase --------------------------------
design_phase() {
  local p t0 dur ans todo
  while :; do
    todo=()
    for p in $(projects); do
      [ -f "$STATE/$p.design.done" ] && continue
      if [ -s "$ROOT/$p/SPEC.md" ]; then   # already designed: no second session
        [ -f "$STATE/design.start" ] || now > "$STATE/design.start"
        approve "$p" || { say "git sync failed for $p (secret found or push failed). Fix it, then press Enter."; read -r _; todo+=(retry); }
      else
        todo+=("$p")
      fi
    done
    [ ${#todo[@]} -eq 0 ] && break
    [ "${todo[0]}" = retry ] && continue
    say "DESIGN (one session): ${todo[*]}. Approve each design, then type /exit."
    t0=$(now); echo "$t0" > "$STATE/design.start"; tlog root design session_start 0
    ( cd "$ROOT" && "$CLAUDE_BIN" --model "$MODEL_DESIGN" --permission-mode acceptEdits "$(design_prompt "${todo[@]}")" )
    dur=$(( $(now) - t0 )); tlog root design session_end "$dur"; say "design session took $(clock "$dur")"
    ans=
    for p in "${todo[@]}"; do [ -s "$ROOT/$p/SPEC.md" ] || ans="$ans $p"; done
    if [ -n "$ans" ]; then
      read -r -p "No SPEC.md for:$ans. [r]eopen session / [s]kip them? " ans
      if [ "$ans" = s ]; then
        for p in "${todo[@]}"; do [ -s "$ROOT/$p/SPEC.md" ] && approve "$p"; done
        break
      fi
    fi
  done
  touch "$STATE/designs.finished"
}

# ----------------------------------- report -----------------------------------
report() {
  local p f="$ROOT/REPORT.md" d b t dur r tot_d=0 tot_b=0 np=0 nb=0
  {
    echo "# Pipeline report"; echo; echo "Generated $(date -Iseconds)."; echo
    echo "| Project | Design done | Design time | Build passed | Tests passing | Build time | Retries |"
    echo "|---|---|---|---|---|---|---|"
    for p in $(projects); do
      np=$((np + 1))
      [ -f "$STATE/$p.design.done" ] && d=yes || d=no
      if [ -f "$STATE/$p.build.done" ]; then b=yes; nb=$((nb + 1))
      elif [ -f "$STATE/$p.build.failed" ]; then b="no (gave up)"; else b=no; fi
      case "$(cat "$STATE/$p.test.rc" 2>/dev/null)" in 0) t=yes;; "") t="n/a";; *) t=no;; esac
      dur=$(cat "$STATE/$p.build.secs" 2>/dev/null || echo 0)
      r=$(( $(cat "$STATE/$p.attempts" 2>/dev/null || echo 1) - 1 )); [ "$r" -lt 0 ] && r=0
      tot_d=$((tot_d + $(cat "$STATE/$p.design.secs" 2>/dev/null || echo 0))); tot_b=$((tot_b + dur))
      printf '| %s | %s | %ss | %s | %s | %ss | %s |\n' "$p" "$d" "$(cat "$STATE/$p.design.secs" 2>/dev/null || echo 0)" "$b" "$t" "$dur" "$r"
    done
    echo; echo "## Totals (from all_timings.log)"; echo
    echo "- Projects: $np; builds passed: $nb"
    echo "- Design time (sum): ${tot_d}s; build time, first attempt to pass (sum): ${tot_b}s"
    awk -F' \\| ' '
      $3=="build" && $4 ~ /_end_/ {ba+=$5; n++}
      $3=="test" {tt+=$5}
      $4=="retry_wait" {rw+=$5; nr++}
      $4=="usage_limit" {ul++}
      $3=="run" && $4=="start" && !s {s=$1}
      {e=$1}
      END {
        printf "- Build attempts: %d (%ds of Claude build time)\n", n, ba
        printf "- Test runs by the orchestrator: %ds\n", tt
        printf "- Retry waits: %d (%ds scheduled); usage limits hit: %d\n", nr, rw, ul
        printf "- First run start: %s; last event: %s\n", s, e
      }' "$TLOG"
    echo; echo "## Token usage (from logs/)"; echo
    python3 "$ROOT/usage.py" "$LOGS" || echo "Token usage unavailable: usage.py failed."
  } > "$f"
  say "wrote $f"
}

status() {
  local p b task t0
  for p in $(projects); do
    if [ -f "$STATE/$p.build.done" ]; then b="passed in $(clock "$(cat "$STATE/$p.build.secs")")"
    elif [ -f "$STATE/$p.build.failed" ]; then b=failed
    elif [ -f "$STATE/$p.task" ]; then read -r task t0 < "$STATE/$p.task"; b="$task running $(clock $(( $(now) - t0 )))"
    elif [ -f "$STATE/$p.build.first" ]; then b="waiting, $(clock "$(since "$STATE/$p.build.first")") since first attempt"
    else b=-; fi
    printf '%-12s design:%-5s attempts:%-2s build: %s\n' "$p" \
      "$([ -f "$STATE/$p.design.done" ] && echo done || echo -)" \
      "$(cat "$STATE/$p.attempts" 2>/dev/null || echo 0)" "$b"
  done
}

# ---------------------------------- dry run -----------------------------------
# Runs the whole pipeline in $ROOT/.dryrun with a stub `claude`, a local bare
# remote and three fake projects: project_1 hits a fake usage limit and
# project_2 fails its tests once. Nothing real is called or pushed.
dry_run() {
  local sb="$ROOT/.dryrun"; rm -rf "$sb"; mkdir -p "$sb/root"
  git init -q --bare "$sb/remote.git"
  cp "$ROOT/orchestrate.sh" "$ROOT/logstream.py" "$ROOT/usage.py" "$ROOT/config.sh" "$ROOT/.gitignore" "$sb/root/"
  cp -r "$ROOT/project_0" "$sb/root/"; rm -f "$sb/root/project_0/"{SPEC.md,timings.log}
  echo "# SPEC (stub, already designed)" > "$sb/root/project_0/SPEC.md"
  for i in 1 2; do mkdir -p "$sb/root/project_$i"; echo "fake requirement $i" > "$sb/root/project_$i/requirements.md"; done
  cat > "$sb/claude" <<'STUB'
#!/usr/bin/env bash
p=$(basename "$PWD")
if [[ " $* " == *" -p "* ]]; then
  n=$(( $(cat ../.stub_$p 2>/dev/null || echo 0) + 1 )); echo $n > ../.stub_$p; sleep 3
  if [ "$p" = project_1 ] && [ $n = 1 ]; then echo "Claude usage limit reached. Your limit will reset at 5pm."; exit 1; fi
  if [ "$p" = project_2 ] && [ $n = 1 ]; then printf '#!/usr/bin/env bash\necho FAIL; exit 1\n' > test.sh
  else printf '#!/usr/bin/env bash\necho stub tests ok\n' > test.sh; fi
  echo "# $p (stub build)" > README.md; echo "ANTHROPIC_API_KEY=sk-ant-FAKEFAKEFAKEFAKEFAKE" > .env
  echo '{"type":"system","subtype":"init","model":"stub-model"}'
  echo '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"echo stub"}}]}}'
  sleep 1
  echo '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","content":"stub"}]}}'
  result() {  # $1 = running cost, $2 = running output tokens; usage is per segment
    echo '{"type":"result","subtype":"success","num_turns":1,"is_error":false,"total_cost_usd":'$1',"usage":{"input_tokens":1,"cache_creation_input_tokens":10,"cache_read_input_tokens":100,"output_tokens":1000},"modelUsage":{"stub-model":{"inputTokens":1,"cacheCreationInputTokens":10,"cacheReadInputTokens":100,"outputTokens":'$2',"costUSD":'$1'}}}'
  }
  result 0.01 1000
  if [ "$p" = project_2 ]; then result 0.02 2000; fi
else
  for d in project_1 project_2; do sleep 2; echo "# SPEC (stub) $d" > $d/SPEC.md; touch $d/.approved; done; sleep 2; echo "stub design done"
fi
STUB
  chmod +x "$sb/claude"
  ( cd "$sb/root" && git init -q -b main && git add -A && git commit -q -m init \
      && git remote add origin "$sb/remote.git" && git push -q origin main ) || return 1
  say "dry run in $sb"
  ORCH_ROOT="$sb/root" CLAUDE_BIN="$sb/claude" PAR=2 LIMIT_PAR=1 WAIT_LIMIT_S=8 WAIT_FAIL_S=4 POLL_S=1 HEARTBEAT_S=2 \
    PROJECTS=auto bash "$sb/root/orchestrate.sh" run
  echo; echo "---- status ----"; ORCH_ROOT="$sb/root" bash "$sb/root/orchestrate.sh" status
  echo "---- remote log ----"; git --git-dir="$sb/remote.git" log --oneline main
  echo "---- .env pushed? ----"; git --git-dir="$sb/remote.git" ls-tree -r --name-only main | grep -E '\.env$' || echo "no (good)"
  echo "---- all_timings.log ----"; cat "$sb/root/all_timings.log"
  echo "---- logs/project_1.log ----"; cat "$sb/root/logs/project_1.log"
  echo "---- REPORT.md ----"; cat "$sb/root/REPORT.md"
}

# ------------------------------------ main ------------------------------------
run() {
  rm -f "$STATE/designs.finished" "$STATE/design.start" "$STATE"/*.task "$STATE"/*.build.pid
  tlog root run start 0; local t0; t0=$(now)
  say "projects: $(projects | tr '\n' ' ')| PAR=$PAR models: $MODEL_DESIGN/$MODEL_BUILD"
  local olog="$LOGS/orchestrator.log"
  # logged to a file so it cannot scribble over the interactive design screen
  dispatcher >> "$olog" 2>&1 & local dpid=$!
  trap 'say "interrupted; state kept in .state, rerun to resume"; kill 0' INT TERM
  design_phase
  say "all designs done; waiting for builds (tail -f logs/*.log to follow)"
  tail -n 40 -f --pid="$dpid" "$olog"
  wait "$dpid"
  tlog root run end $(( $(now) - t0 )); say "run finished in $(clock $(( $(now) - t0 )))"
  report
  # each timings.log gains lines after its project's build commit, so include them here
  local p tlogs=()
  for p in $(projects); do tlogs+=("$p/timings.log"); done
  git_sync "pipeline report and timings" REPORT.md all_timings.log logs logstream.py usage.py orchestrate.sh config.sh README.md .gitignore "${tlogs[@]}" \
    || say "final git sync failed"
}

case "${1:-run}" in
  run) run ;;
  status) status ;;
  report) report ;;
  dry-run) dry_run ;;
  *) echo "usage: $0 [run|status|report|dry-run]"; exit 1 ;;
esac
