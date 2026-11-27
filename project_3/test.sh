#!/usr/bin/env bash
# Install dependencies if needed, then run every test. Offline after first setup.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

die() {
  echo "test.sh: $*" >&2
  exit 1
}

# 1. Tools
node_version="v$(cat .nvmrc)"

node_ok() {
  command -v node >/dev/null 2>&1 && [ "$(node --version)" = "$node_version" ]
}

if ! node_ok; then
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  if [ -s "$NVM_DIR/nvm.sh" ]; then
    # nvm is not safe under strict mode.
    set +euo pipefail
    . "$NVM_DIR/nvm.sh" --no-use
    nvm use "${node_version#v}" >/dev/null
    set -euo pipefail
  fi
fi
node_ok || die "Node.js $node_version is required (try: nvm install ${node_version#v})"
command -v npm >/dev/null 2>&1 || die "npm is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

# 2. Setup: each copy of a pinned file records what is installed.
export npm_config_update_notifier=false

venv=backend/.venv
if [ ! -x "$venv/bin/python" ] || ! cmp -s backend/requirements.txt "$venv/requirements.txt"; then
  rm -f "$venv/requirements.txt"
  python3 -m venv backend/.venv
  backend/.venv/bin/python -m pip install -r backend/requirements.txt
  cp backend/requirements.txt "$venv/requirements.txt"
fi

if ! cmp -s frontend/package-lock.json frontend/node_modules/package-lock.json; then
  npm ci --prefix frontend
  cp frontend/package-lock.json frontend/node_modules/package-lock.json
fi

# 3. Isolate
unset DOCS_DIR BACKEND_URL LLM_PROXY_URL LLM_PROXY_API_KEY LLM_PROXY_MODEL
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NEXT_TELEMETRY_DISABLED=1

# 4. Test
backend/.venv/bin/python -m pytest backend/tests
npm --prefix frontend test
npm --prefix frontend run build
backend/.venv/bin/python -m pytest e2e

echo "All tests passed."
