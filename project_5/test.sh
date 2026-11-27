#!/usr/bin/env bash
# Install what is missing, then run every test. Stops at the first failure.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

fail() {
  echo "test.sh: $*" >&2
  exit 1
}

# 1. Tools
node_version="v$(tr -d '[:space:]' <.nvmrc)"
right_node() {
  command -v node >/dev/null 2>&1 && [ "$(node --version)" = "$node_version" ]
}
if ! right_node; then
  nvm_script="${NVM_DIR:-$HOME/.nvm}/nvm.sh"
  if [ -s "$nvm_script" ]; then
    export NVM_DIR="${nvm_script%/nvm.sh}"
    # nvm is not safe under "set -u" (or "set -e"); the check below catches errors.
    set +eu
    # shellcheck disable=SC1090
    . "$nvm_script" --no-use
    nvm use "$node_version" >/dev/null
    set -eu
  fi
fi
right_node || fail "Node.js $node_version is not available (try: nvm install ${node_version#v})"
command -v python3 >/dev/null 2>&1 || fail "python3 is not available"

# 2. Setup: only when missing, or when the pinned file differs from the stored copy.
python=backend/.venv/bin/python
backend_stamp=backend/.venv/installed-requirements.txt
if [ ! -x "$python" ] || ! cmp -s backend/requirements.txt "$backend_stamp"; then
  rm -f "$backend_stamp"
  python3 -m venv backend/.venv
  "$python" -m pip install -r backend/requirements.txt
  cp backend/requirements.txt "$backend_stamp"
fi

frontend_stamp=frontend/node_modules/.installed-package-lock.json
if ! cmp -s frontend/package-lock.json "$frontend_stamp"; then
  npm ci --prefix frontend
  cp frontend/package-lock.json "$frontend_stamp"
fi

# 3. Isolate the tests from the caller's settings and from the network.
unset DOCS_DIR BACKEND_URL LLM_PROXY_URL LLM_PROXY_API_KEY LLM_PROXY_MODEL
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NEXT_TELEMETRY_DISABLED=1
export npm_config_update_notifier=false

# 4. Test
"$python" -m pytest backend/tests
npm --prefix frontend test
npm --prefix frontend run build
"$python" -m pytest e2e

echo "All tests passed."
