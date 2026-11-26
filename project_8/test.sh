#!/usr/bin/env bash
# Sets up dependencies when needed, then runs every test. Offline after first setup.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

fail() {
  echo "test.sh: $*" >&2
  exit 1
}

# 1. Tools
wanted_node="v$(<.nvmrc)"
node_ok() { command -v node >/dev/null && [[ "$(node --version)" == "$wanted_node" ]]; }
if ! node_ok; then
  nvm_script="${NVM_DIR:-$HOME/.nvm}/nvm.sh"
  if [[ -s "$nvm_script" ]]; then
    set +eu
    # shellcheck disable=SC1090
    source "$nvm_script" --no-use
    nvm use "$(<.nvmrc)" >/dev/null
    set -eu
  fi
fi
node_ok || fail "Node.js $wanted_node is not available (install it with: nvm install ${wanted_node#v})"
command -v npm >/dev/null || fail "npm is not available"
command -v python3 >/dev/null || fail "python3 is not available"

# 2. Setup: only when missing or when the pinned file changed. The stamp is written
# last, so an interrupted install is repeated.
venv_stamp=backend/.venv/requirements.txt
if [[ ! -x backend/.venv/bin/python ]] || ! cmp -s backend/requirements.txt "$venv_stamp"; then
  rm -f "$venv_stamp"
  python3 -m venv backend/.venv
  backend/.venv/bin/python -m pip install -r backend/requirements.txt
  cp backend/requirements.txt "$venv_stamp"
fi
npm_stamp=frontend/node_modules/.test-sh-package-lock.json
if ! cmp -s frontend/package-lock.json "$npm_stamp"; then
  npm ci --prefix frontend
  cp frontend/package-lock.json "$npm_stamp"
fi

# 3. Isolate from the caller's settings
unset DOCS_DIR BACKEND_URL LLM_PROXY_URL LLM_PROXY_API_KEY LLM_PROXY_MODEL
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NEXT_TELEMETRY_DISABLED=1

# 4. Test
backend/.venv/bin/python -m pytest backend/tests
npm --prefix frontend test
npm --prefix frontend run build
backend/.venv/bin/python -m pytest e2e
