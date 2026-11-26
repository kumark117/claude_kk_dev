#!/usr/bin/env bash
# Install what is missing, then run every test. Stops at the first failure.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

fail() {
  echo "test.sh: $*" >&2
  exit 1
}

step() {
  echo
  echo "==> $*"
}

# 1. Tools
node_version="v$(tr -d '[:space:]' <.nvmrc)"
current_node() {
  node --version 2>/dev/null || true
}

if [ "$(current_node)" != "$node_version" ]; then
  nvm_script="${NVM_DIR:-${HOME:-}/.nvm}/nvm.sh"
  if [ -s "$nvm_script" ]; then
    # nvm is not safe under strict mode.
    set +euo pipefail
    # shellcheck disable=SC1090
    . "$nvm_script" --no-use
    nvm use "$node_version" >/dev/null
    set -euo pipefail
  fi
fi
[ "$(current_node)" = "$node_version" ] ||
  fail "Node.js $node_version is required (try: nvm install ${node_version#v})"
command -v npm >/dev/null || fail "npm is not available"
command -v python3 >/dev/null || fail "python3 is not available"

# 2. Setup, only when missing or when a pinned file changed. The copy of the
#    pinned file is written last, so a failed install is retried next time.
venv=backend/.venv
if ! "$venv/bin/python" -c '' 2>/dev/null ||
  ! cmp -s backend/requirements.txt "$venv/requirements.txt"; then
  step "Installing backend dependencies"
  python3 -m venv --clear "$venv"
  "$venv/bin/python" -m pip install -r backend/requirements.txt
  cp backend/requirements.txt "$venv/requirements.txt"
fi

installed_lock=frontend/node_modules/.installed-package-lock.json
if ! cmp -s frontend/package-lock.json "$installed_lock"; then
  step "Installing frontend dependencies"
  npm ci --prefix frontend
  cp frontend/package-lock.json "$installed_lock"
fi

# 3. Isolate the tests from the caller's settings.
unset DOCS_DIR BACKEND_URL LLM_PROXY_URL LLM_PROXY_API_KEY LLM_PROXY_MODEL
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NEXT_TELEMETRY_DISABLED=1
export npm_config_update_notifier=false

# 4. Tests
step "Backend unit tests"
"$venv/bin/python" -m pytest backend/tests

step "Frontend unit tests"
npm --prefix frontend test

step "Frontend build"
npm --prefix frontend run build

step "End-to-end tests"
"$venv/bin/python" -m pytest e2e

step "All tests passed"
