# Orchestrator config. Environment variables override these values,
# e.g.  PAR=1 ./orchestrate.sh

: "${PLAN:=pro}"                 # pro | max
if [ "$PLAN" = max ]; then : "${PAR:=4}"; else : "${PAR:=2}"; fi   # concurrent builds
: "${LIMIT_PAR:=1}"              # concurrency while a usage limit is active
: "${MODEL_DESIGN:=sonnet}"      # model alias for design sessions
: "${MODEL_BUILD:=sonnet}"       # model alias for headless builds
: "${MAX_TURNS:=80}"             # turn cap per build attempt
: "${MAX_RETRIES:=6}"            # retries after the first build attempt
: "${WAIT_LIMIT_S:=1800}"        # wait after a usage limit (30 min)
: "${WAIT_FAIL_S:=180}"          # wait after any other failure (3 min)
: "${BUILD_TIMEOUT_S:=3600}"     # hard timeout per build attempt
: "${TEST_TIMEOUT_S:=900}"       # hard timeout for the orchestrator's test run
: "${POLL_S:=15}"                # scheduler poll interval
: "${PROJECTS:=auto}"            # auto = every project_*/requirements.md, or "0 2 3"
: "${GIT_REMOTE:=origin}"
: "${GIT_BRANCH:=main}"
: "${CLAUDE_BIN:=claude}"
