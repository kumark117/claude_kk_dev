#!/usr/bin/env bash
# Install dependencies when needed, then run every test. Offline after setup.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

fail() {
  echo "test.sh: $*" >&2
  exit 1
}

step() {
  printf '\n==> %s\n' "$*"
}

# 1. Tools
node_wanted="v$(<.nvmrc)"
node_ok() {
  command -v node >/dev/null 2>&1 && [[ "$(node --version)" == "$node_wanted" ]]
}
if ! node_ok; then
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  if [[ -s "$NVM_DIR/nvm.sh" ]]; then
    set +eu
    # shellcheck disable=SC1091
    source "$NVM_DIR/nvm.sh" --no-use
    nvm use "$(<.nvmrc)" >/dev/null
    set -eu
  fi
fi
node_ok || fail "Node.js $node_wanted is required (install it with: nvm install $(<.nvmrc))."
command -v python3 >/dev/null 2>&1 || fail "python3 is required but was not found."

# 2. Setup: only when missing or when the pinned file changed.
venv_stamp=backend/.venv/.installed-requirements.txt
if [[ ! -x backend/.venv/bin/python ]] || ! cmp -s backend/requirements.txt "$venv_stamp"; then
  step "Installing backend dependencies"
  rm -f "$venv_stamp"
  python3 -m venv backend/.venv
  backend/.venv/bin/python -m pip install -r backend/requirements.txt
  cp backend/requirements.txt "$venv_stamp"
fi

npm_stamp=frontend/node_modules/.installed-package-lock.json
if ! cmp -s frontend/package-lock.json "$npm_stamp"; then
  step "Installing frontend dependencies"
  npm ci --prefix frontend
  cp frontend/package-lock.json "$npm_stamp"
fi

# 3. Isolate from the caller's settings.
unset DOCS_DIR BACKEND_URL LLM_PROXY_URL LLM_PROXY_API_KEY LLM_PROXY_MODEL
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NEXT_TELEMETRY_DISABLED=1

# 4. Test
step "Backend unit tests"
backend/.venv/bin/python -m pytest backend/tests
step "Frontend unit tests"
npm --prefix frontend test
step "Frontend build"
npm --prefix frontend run build
step "End-to-end tests"
backend/.venv/bin/python -m pytest e2e

step "All tests passed"
