#!/usr/bin/env bash
# Install dependencies when needed, then run every test. Offline after first setup.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

fail() {
  echo "test.sh: $*" >&2
  exit 1
}

# 1. Tools
node_version="$(cat .nvmrc)"
node_is_right() {
  command -v node >/dev/null 2>&1 && [ "$(node --version)" = "v$node_version" ]
}
if ! node_is_right; then
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  if [ -s "$NVM_DIR/nvm.sh" ]; then
    set +eu
    # shellcheck disable=SC1091
    . "$NVM_DIR/nvm.sh" --no-use
    nvm use "$node_version" >/dev/null
    set -eu
  fi
fi
node_is_right || fail "Node.js $node_version is required (try: nvm install $node_version)"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"

# 2. Setup: only when missing or when the pinned file changed. The stamp is a
# copy of that file, written only after a successful install.
python=backend/.venv/bin/python
python_stamp=backend/.venv/requirements.txt
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

# 3. Isolate from the caller's settings.
unset DOCS_DIR BACKEND_URL LLM_PROXY_URL LLM_PROXY_API_KEY LLM_PROXY_MODEL \
  HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NEXT_TELEMETRY_DISABLED=1

# 4. Test. The end-to-end tests start their own servers and stop them on exit.
"$python" -m pytest backend/tests
npm --prefix frontend test
npm --prefix frontend run build
"$python" -m pytest e2e
echo "All tests passed."
