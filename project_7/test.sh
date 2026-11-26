#!/usr/bin/env bash
# Run every test. Network access is needed only for first-time dependency setup.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

step() { printf '\n==> %s\n' "$*"; }
fail() { printf 'test.sh: %s\n' "$*" >&2; exit 1; }

# 1. Tools
node_version="v$(<.nvmrc)"
have_node() { command -v node >/dev/null 2>&1 && [ "$(node --version)" = "$node_version" ]; }

if ! have_node; then
  nvm_script="${NVM_DIR:-$HOME/.nvm}/nvm.sh"
  if [ -s "$nvm_script" ]; then
    export NVM_DIR="${nvm_script%/nvm.sh}"
    # nvm is not safe under "set -u" (nor "set -e").
    set +eu
    # shellcheck disable=SC1090
    . "$nvm_script" --no-use
    nvm use "$node_version" >/dev/null
    set -eu
  fi
fi
have_node || fail "Node.js $node_version is not available. Install it, e.g. 'nvm install ${node_version#v}'."
command -v python3 >/dev/null 2>&1 || fail "python3 is not available."

# 2. Setup: only when missing or when the pinned file changed. The copy stored in
# the installed folder is written last, so a failed install is retried.
python=backend/.venv/bin/python
python_stamp=backend/.venv/requirements.installed.txt
if [ ! -x "$python" ] || ! cmp -s backend/requirements.txt "$python_stamp"; then
  step "Installing backend dependencies"
  rm -f "$python_stamp"
  python3 -m venv backend/.venv
  "$python" -m pip install -r backend/requirements.txt
  cp backend/requirements.txt "$python_stamp"
fi

node_stamp=frontend/node_modules/.package-lock.installed.json
if ! cmp -s frontend/package-lock.json "$node_stamp"; then
  step "Installing frontend dependencies"
  npm ci --prefix frontend
  cp frontend/package-lock.json "$node_stamp"
fi

# 3. Isolate from the caller's settings.
unset DOCS_DIR BACKEND_URL LLM_PROXY_URL LLM_PROXY_API_KEY LLM_PROXY_MODEL
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NEXT_TELEMETRY_DISABLED=1

# 4. Test
step "Backend unit tests"
"$python" -m pytest backend/tests
step "Frontend unit tests"
npm --prefix frontend test
step "Frontend build"
npm --prefix frontend run build
step "End-to-end tests"
"$python" -m pytest e2e

step "All tests passed"
