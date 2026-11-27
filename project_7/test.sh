#!/usr/bin/env bash
# Install anything missing, then run every test. Only first-time setup needs a network.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

fail() {
  echo "test.sh: $*" >&2
  exit 1
}

step() {
  printf '\n==> %s\n' "$*"
}

# NODE_ENV=production would skip dev dependencies and break the component tests.
unset NODE_ENV

# 1. Tools
node_version="v$(<.nvmrc)"
if [[ "$(node --version 2>/dev/null || true)" != "$node_version" ]]; then
  nvm_script="${NVM_DIR:-${HOME:-}/.nvm}/nvm.sh"
  if [[ -s "$nvm_script" ]]; then
    set +euo pipefail # nvm is not safe under strict mode
    # shellcheck disable=SC1090
    source "$nvm_script" --no-use
    nvm use "$node_version" >/dev/null
    set -euo pipefail
  fi
fi
[[ "$(node --version 2>/dev/null || true)" == "$node_version" ]] ||
  fail "Node.js $node_version is required (install it with: nvm install $node_version)"
command -v npm >/dev/null || fail "npm is not available"
command -v python3 >/dev/null || fail "python3 is not available"

# 2. Setup, only when missing or when the pinned file has changed
python=backend/.venv/bin/python
python_stamp=backend/.venv/requirements.installed
if [[ ! -x "$python" ]] || ! cmp -s backend/requirements.txt "$python_stamp"; then
  step "Installing backend dependencies"
  rm -f "$python_stamp"
  python3 -m venv backend/.venv
  "$python" -m pip install -r backend/requirements.txt
  cp backend/requirements.txt "$python_stamp"
fi

node_stamp=frontend/node_modules/.package-lock.installed
if ! cmp -s frontend/package-lock.json "$node_stamp"; then
  step "Installing frontend dependencies"
  npm ci --prefix frontend
  cp frontend/package-lock.json "$node_stamp"
fi

# 3. Isolate the tests from the caller's settings
unset DOCS_DIR BACKEND_URL LLM_PROXY_URL LLM_PROXY_API_KEY LLM_PROXY_MODEL
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NEXT_TELEMETRY_DISABLED=1
export npm_config_update_notifier=false # npm would otherwise contact the registry

# 4. Test. The end-to-end tests start their own servers and always stop them.
step "Backend unit tests"
"$python" -m pytest backend/tests

step "Frontend unit tests"
npm --prefix frontend test

step "Frontend build"
npm --prefix frontend run build

step "End-to-end tests"
"$python" -m pytest e2e

step "All tests passed"
