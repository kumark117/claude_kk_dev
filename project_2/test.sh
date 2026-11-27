#!/usr/bin/env bash
# Install dependencies when needed, then run every test. See SPEC.md section 9.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

fail() {
  echo "test.sh: $*" >&2
  exit 1
}

# 1. Tools
node_version="v$(<.nvmrc)"
node_matches() {
  command -v node >/dev/null 2>&1 && [[ "$(node --version)" == "$node_version" ]]
}
if ! node_matches; then
  nvm_script="${NVM_DIR:-$HOME/.nvm}/nvm.sh"
  if [[ -s "$nvm_script" ]]; then
    # nvm is not safe under strict mode.
    set +euo pipefail
    # shellcheck disable=SC1090
    source "$nvm_script" --no-use
    nvm use "${node_version#v}" >/dev/null
    set -euo pipefail
  fi
fi
node_matches || fail "Node.js $node_version is required (try: nvm install ${node_version#v})"
command -v npm >/dev/null 2>&1 || fail "npm is not available"
command -v python3 >/dev/null 2>&1 || fail "python3 is not available"

# 2. Setup, only when missing or when the pinned file changed
venv=backend/.venv
if [[ ! -x $venv/bin/python ]] || ! cmp -s backend/requirements.txt "$venv/requirements.txt"; then
  echo "== Installing backend dependencies"
  python3 -m venv "$venv"
  "$venv/bin/python" -m pip install -r backend/requirements.txt
  cp backend/requirements.txt "$venv/requirements.txt"
fi
if ! cmp -s frontend/package-lock.json frontend/node_modules/package-lock.json; then
  echo "== Installing frontend dependencies"
  npm ci --prefix frontend
  cp frontend/package-lock.json frontend/node_modules/package-lock.json
fi

# 3. Isolate from the caller's environment
unset DOCS_DIR BACKEND_URL LLM_PROXY_URL LLM_PROXY_API_KEY LLM_PROXY_MODEL
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NEXT_TELEMETRY_DISABLED=1

# 4. Test
echo "== Backend unit tests"
"$venv/bin/python" -m pytest backend/tests
echo "== Frontend unit tests"
npm --prefix frontend test
echo "== Frontend build"
npm --prefix frontend run build
echo "== End-to-end tests"
"$venv/bin/python" -m pytest e2e
echo "== All tests passed"
