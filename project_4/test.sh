#!/usr/bin/env bash
# Set up dependencies if needed, then run every test. Offline once installed.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

fail() {
  echo "test.sh: $*" >&2
  exit 1
}

# 1. Tools
node_version="$(tr -d '[:space:]' < .nvmrc)"
have_node() {
  command -v node >/dev/null 2>&1 && [ "$(node --version)" = "v$node_version" ]
}
if ! have_node; then
  nvm_script="${NVM_DIR:-$HOME/.nvm}/nvm.sh"
  if [ -s "$nvm_script" ]; then
    # nvm is not safe under "set -eu".
    set +eu
    # shellcheck disable=SC1090
    . "$nvm_script" --no-use
    nvm use "$node_version" >/dev/null
    set -eu
  fi
fi
have_node || fail "Node.js $node_version is not available (install it with: nvm install $node_version)"
command -v python3 >/dev/null 2>&1 || fail "python3 is not available"

# 2. Setup: only when missing or when the pinned file changed.
python=backend/.venv/bin/python
backend_stamp=backend/.venv/requirements.txt
if [ ! -x "$python" ] || ! cmp -s backend/requirements.txt "$backend_stamp"; then
  rm -f "$backend_stamp"
  python3 -m venv backend/.venv
  "$python" -m pip install -r backend/requirements.txt
  cp backend/requirements.txt "$backend_stamp"
fi
frontend_stamp=frontend/node_modules/.package-lock.stamp
if ! cmp -s frontend/package-lock.json "$frontend_stamp"; then
  npm ci --prefix frontend
  cp frontend/package-lock.json "$frontend_stamp"
fi

# 3. Isolate from the caller's settings.
unset DOCS_DIR BACKEND_URL LLM_PROXY_URL LLM_PROXY_API_KEY LLM_PROXY_MODEL \
  HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NEXT_TELEMETRY_DISABLED=1

# 4. Test. The end-to-end tests start their own servers and stop them.
"$python" -m pytest backend/tests
npm --prefix frontend test
npm --prefix frontend run build
"$python" -m pytest e2e

echo "All tests passed."
