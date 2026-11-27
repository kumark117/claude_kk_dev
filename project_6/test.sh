#!/usr/bin/env bash
# Run every test. Network access is only needed for first-time setup.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

fail() {
  echo "test.sh: $*" >&2
  exit 1
}

step() {
  printf '\n==> %s\n' "$*"
}

# 1. Tools
node_version="v$(tr -d '[:space:]' <.nvmrc)"

node_ok() {
  command -v node >/dev/null 2>&1 && [ "$(node --version)" = "$node_version" ]
}

if ! node_ok; then
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  if [ -s "$NVM_DIR/nvm.sh" ]; then
    # nvm is not safe under "set -u".
    set +euo pipefail
    . "$NVM_DIR/nvm.sh" --no-use
    nvm use "$node_version" >/dev/null
    set -euo pipefail
  fi
fi
node_ok || fail "Node.js $node_version is not available. Install it with: nvm install $node_version"
command -v npm >/dev/null 2>&1 || fail "npm is not available."
command -v python3 >/dev/null 2>&1 || fail "python3 is not available."

# 2. Setup, only when missing or when the pinned file changed
python=backend/.venv/bin/python
pip_stamp=backend/.venv/requirements.txt
if [ ! -x "$python" ] || ! cmp -s backend/requirements.txt "$pip_stamp"; then
  step "Installing backend dependencies"
  rm -f "$pip_stamp"
  python3 -m venv backend/.venv
  "$python" -m pip install -r backend/requirements.txt
  cp backend/requirements.txt "$pip_stamp"
fi

# "npm ci" removes node_modules first, which also removes an old stamp.
npm_stamp=frontend/node_modules/.package-lock.stamp.json
if ! cmp -s frontend/package-lock.json "$npm_stamp"; then
  step "Installing frontend dependencies"
  npm ci --prefix frontend
  cp frontend/package-lock.json "$npm_stamp"
fi

# 3. Isolate from the caller's settings
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
