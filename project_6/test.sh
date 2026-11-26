#!/usr/bin/env bash
# Installs dependencies when needed, then runs every test.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

fail() {
  echo "test.sh: $*" >&2
  exit 1
}

# 1. Tools
node_version="$(<.nvmrc)"
have_node() {
  command -v node >/dev/null 2>&1 && [ "$(node --version)" = "v$node_version" ]
}
if ! have_node; then
  nvm_script="${NVM_DIR:-$HOME/.nvm}/nvm.sh"
  if [ -s "$nvm_script" ]; then
    # nvm is not safe under "set -u" or "set -e".
    set +eu
    . "$nvm_script" --no-use
    nvm use "$node_version" >/dev/null
    set -eu
  fi
fi
have_node || fail "Node.js $node_version is not available (install it with: nvm install $node_version)"
command -v python3 >/dev/null 2>&1 || fail "python3 is not available"

# 2. Setup: only when missing, or when the pinned file has changed.
venv=backend/.venv
venv_stamp="$venv/requirements.txt"
if [ ! -x "$venv/bin/python" ] || ! cmp -s backend/requirements.txt "$venv_stamp"; then
  rm -f "$venv_stamp"
  python3 -m venv "$venv"
  "$venv/bin/python" -m pip install -r backend/requirements.txt
  cp backend/requirements.txt "$venv_stamp"
fi

npm_stamp=frontend/node_modules/package-lock.json
if ! cmp -s frontend/package-lock.json "$npm_stamp"; then
  npm ci --prefix frontend
  cp frontend/package-lock.json "$npm_stamp"
fi

# 3. Isolate from the caller's settings.
unset DOCS_DIR BACKEND_URL LLM_PROXY_URL LLM_PROXY_API_KEY LLM_PROXY_MODEL
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NEXT_TELEMETRY_DISABLED=1
export npm_config_update_notifier=false # no version check over the network

# 4. Test
"$venv/bin/python" -m pytest backend/tests
npm --prefix frontend test
npm --prefix frontend run build
"$venv/bin/python" -m pytest e2e

echo "All tests passed."
