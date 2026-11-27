#!/usr/bin/env bash
# Set up dependencies when needed, then run every test. Stops at the first failure.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

fail() {
  echo "test.sh: $*" >&2
  exit 1
}

# 1. Tools
want_node="v$(tr -d '[:space:]' <.nvmrc)"

node_ok() {
  command -v node >/dev/null 2>&1 && [ "$(node --version)" = "$want_node" ]
}

if ! node_ok; then
  export NVM_DIR="${NVM_DIR:-${HOME:-}/.nvm}"
  if [ -s "$NVM_DIR/nvm.sh" ]; then
    # nvm is not safe under strict mode.
    set +euo pipefail
    . "$NVM_DIR/nvm.sh" --no-use
    nvm use "${want_node#v}" >/dev/null
    set -euo pipefail
  fi
fi
node_ok || fail "Node.js ${want_node#v} is required (see .nvmrc); install it with: nvm install ${want_node#v}"
command -v npm >/dev/null 2>&1 || fail "npm is required (it comes with Node.js)"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"

# 2. Setup, only when missing or when the pinned file changed
export NEXT_TELEMETRY_DISABLED=1

python=backend/.venv/bin/python
python_stamp=backend/.venv/requirements.installed
if [ ! -x "$python" ] || ! cmp -s backend/requirements.txt "$python_stamp"; then
  rm -f "$python_stamp"
  python3 -m venv backend/.venv
  "$python" -m pip install -r backend/requirements.txt
  cp backend/requirements.txt "$python_stamp"
fi

node_stamp=frontend/node_modules/.installed-package-lock.json
if ! cmp -s frontend/package-lock.json "$node_stamp"; then
  npm ci --prefix frontend
  cp frontend/package-lock.json "$node_stamp"
fi

# 3. Isolate the tests from the caller's environment
unset DOCS_DIR BACKEND_URL LLM_PROXY_URL LLM_PROXY_API_KEY LLM_PROXY_MODEL
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
# Stops npm from going online to look for a newer npm.
export npm_config_update_notifier=false

# 4. Tests
"$python" -m pytest backend/tests
npm --prefix frontend test
npm --prefix frontend run build
"$python" -m pytest e2e

echo "All tests passed."
