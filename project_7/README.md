# Minimal RAG

A Next.js page sends a question to a FastAPI server, which retrieves relevant
text from local files (BM25) and passes it to an LLM. The LLM is a built-in stub
by default, with an optional OpenAI-compatible proxy. See [SPEC.md](SPEC.md).

## Requirements

- Node.js 26.13.1 (see `.nvmrc`) and npm 11.20.0
- Python 3.14 with `venv`

## Setup

Run these from the project root (the folder containing this file).
`./test.sh` also does this setup for you.

```bash
nvm use
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm ci --prefix frontend
```

## Run

Terminal 1, the backend on port 8000:

```bash
backend/.venv/bin/python -m uvicorn app.main:create_app --factory \
  --app-dir backend --host 127.0.0.1 --port 8000
```

Terminal 2, the frontend:

```bash
npm --prefix frontend run dev -- -H 127.0.0.1
```

Open the address that Next.js prints (normally <http://127.0.0.1:3000>) and ask,
for example, "How does retrieval work?". You should see a `[stub]` answer and its
sources. An unrelated question, such as "Explain quantum chromodynamics", shows a
fallback answer and "No matching sources."

If port 8000 is taken, give the backend another port and start the frontend with
`BACKEND_URL=http://127.0.0.1:<port>`. For another frontend port, add
`-p <port>` after `-H 127.0.0.1`.

For a production build, run `npm --prefix frontend run build`, then
`npm --prefix frontend run start -- -H 127.0.0.1`.

## Documents

The backend reads `*.md` and `*.txt` files directly inside `data/`, or inside
`DOCS_DIR` if it is set (a relative path is resolved against the directory the
backend is started from). Restart the backend after changing documents.

Search matches keywords only, so synonyms are not found.

## Environment variables

| Name | Used by | Purpose |
|---|---|---|
| `DOCS_DIR` | backend | Documents folder |
| `LLM_PROXY_URL` | backend | Enables proxy mode; API base address, including any version path |
| `LLM_PROXY_API_KEY` | backend | Optional bearer key |
| `LLM_PROXY_MODEL` | backend | Model name; required in proxy mode |
| `BACKEND_URL` | frontend server | FastAPI base address (default: port 8000 on `127.0.0.1`) |
| `NEXT_TELEMETRY_DISABLED` | `test.sh` | Turns off Next.js telemetry (set by the script) |

All are optional in stub mode. Set them in the shell that starts the server:
**the backend does not load `.env` files.** `.env.example` lists the names. Never
commit secrets.

To use proxy mode, export `LLM_PROXY_URL` and `LLM_PROXY_MODEL` (and
`LLM_PROXY_API_KEY` if the endpoint needs one) before starting the backend. It
posts to `<LLM_PROXY_URL>/chat/completions`.

## Test

```bash
./test.sh
```

It installs missing or outdated dependencies, then runs the backend unit tests,
the frontend unit tests, the frontend build and the end-to-end tests, stopping at
the first failure. Only the first-time installation needs network access. Tests
use mocks and fixtures, connect only to `127.0.0.1`, and pick free ports.

A `StarletteDeprecationWarning` about `httpx2` is expected.

## Choices made while building

Points that the specification left open:

- `.gitignore` ignores `.env*` as specified, but keeps `.env.example`. It also
  ignores generated TypeScript files (`next-env.d.ts`, `*.tsbuildinfo`).
- A UTF-8 byte order mark is ignored, so it cannot hide a first-line heading.
- FastAPI's generated documentation pages are turned off; only `/health` and
  `/ask` are served.
- `test.sh` also unsets `NODE_ENV` (a `production` value would break setup and
  tests) and turns off npm's update notifier (a network call).
- `next.config.ts` sets `agentRules: false` so `next dev` does not write an
  `AGENTS.md` file into the project.
- The browser itself is not automated. The page's behavior is covered by
  component tests, and the server path by the end-to-end tests.
