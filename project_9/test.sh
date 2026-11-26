#!/usr/bin/env bash
# Set up dependencies when needed, then run every test. Works offline once set up.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# 1. Tools
wanted_node="v$(<.nvmrc)"
node_version() { command -v node >/dev/null 2>&1 && node --version; }

if [[ "$(node_version || true)" != "$wanted_node" ]]; then
  nvm_script="${NVM_DIR:-$HOME/.nvm}/nvm.sh"
  if [[ -s "$nvm_script" ]]; then
    # nvm is not safe under `set -eu`; failures are caught by the check below.
    set +eu
    # shellcheck disable=SC1090
    source "$nvm_script" --no-use
    nvm use "$(<.nvmrc)" >/dev/null
    set -eu
  fi
fi
if [[ "$(node_version || true)" != "$wanted_node" ]]; then
  echo "error: Node.js $wanted_node is required (install it, e.g. 'nvm install')." >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is not available." >&2
  exit 1
fi

# 2. Setup: only when missing or when the pinned file changed.
python=backend/.venv/bin/python
python_stamp=backend/.venv/requirements.txt
if [[ ! -x $python ]] || ! cmp -s backend/requirements.txt "$python_stamp"; then
  python3 -m venv backend/.venv
  "$python" -m pip install -r backend/requirements.txt
  cp backend/requirements.txt "$python_stamp"
fi

node_stamp=frontend/node_modules/.installed-package-lock.json
if ! cmp -s frontend/package-lock.json "$node_stamp"; then
  npm ci --prefix frontend
  cp frontend/package-lock.json "$node_stamp"
fi

# 3. Isolate the tests from the caller's settings.
unset DOCS_DIR BACKEND_URL LLM_PROXY_URL LLM_PROXY_API_KEY LLM_PROXY_MODEL
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NEXT_TELEMETRY_DISABLED=1

# 4. Test
"$python" -m pytest backend/tests
npm --prefix frontend test
npm --prefix frontend run build
"$python" -m pytest e2e
