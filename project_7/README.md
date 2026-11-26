# Minimal RAG

A Next.js page sends a question to a FastAPI server. The server retrieves relevant
text from local files (BM25) and passes it to an LLM: a built-in stub by default,
or an optional OpenAI-compatible proxy. See [SPEC.md](SPEC.md).

## Requirements

- Node.js 26.13.1 (see `.nvmrc`) with npm 11.20.0
- Python 3.14.4 with `venv`

## Setup

From the project root:

```bash
nvm use
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm ci --prefix frontend
```

## Usage

Start the backend and the frontend in two terminals, both from the project root.
No environment variables and no API key are needed.

Terminal 1, the backend on port 8000:

```bash
backend/.venv/bin/python -m uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000
```

Terminal 2, the frontend:

```bash
npm --prefix frontend run dev
```

Open the address that Next.js prints and ask about the sample documents in
`data/`, for example "Why is Mars called the red planet?". The page shows a
`[stub]` answer and the sources behind it.

If port 8000 is taken, pick another one for `--port` and start the frontend with
`BACKEND_URL` set to `http://127.0.0.1:<port>`. For another frontend port, use
`npm --prefix frontend run dev -- --port <port>`.

For a production build, use `npm --prefix frontend run build` and then
`npm --prefix frontend start`.

### Documents

The backend reads the `*.md` and `*.txt` files directly inside the documents
folder (UTF-8, no subfolders) once, at startup. Restart it after changing them.

### Environment variables

| Name | Used by | Purpose |
|---|---|---|
| `DOCS_DIR` | backend | Documents folder (default: `data/`) |
| `LLM_PROXY_URL` | backend | Enables proxy mode; API base address, including any version path |
| `LLM_PROXY_API_KEY` | backend | Optional bearer key |
| `LLM_PROXY_MODEL` | backend | Model name; required in proxy mode |
| `BACKEND_URL` | frontend server | FastAPI base address (default: port 8000 on `127.0.0.1`) |
| `NEXT_TELEMETRY_DISABLED` | `test.sh` | Turns off Next.js telemetry |

The backend does **not** load `.env` files: export its variables in the shell that
starts it. `.env.example` lists the names. Never commit secrets.

In proxy mode the backend sends the question and the retrieved text to
`<LLM_PROXY_URL>/chat/completions`.

## Tests

```bash
./test.sh
```

This installs dependencies when they are missing or their pinned file changed,
then runs the backend unit tests, the frontend unit tests, the frontend build and
the end-to-end tests. It stops at the first failure. Only the first-time setup
needs network access; the tests use mocks, fixtures and loopback connections on
free ports. No real LLM is called.

`StarletteDeprecationWarning` about `httpx2` is expected.

## Choices made while building

Points the specification left open:

- Sample documents are in `data/`, test fixtures in `e2e/fixtures/docs/`.
- `test.sh` also fails if Node.js is present but not the `.nvmrc` version and nvm
  cannot select it. It tracks `backend/requirements.txt` and
  `frontend/package-lock.json` to decide when to reinstall.
- A UTF-8 byte order mark at the start of a document is ignored.
- Redirects from the LLM proxy count as failures (non-2xx).
- A trailing slash on `BACKEND_URL` is ignored.
- The page sends the question trimmed.
- Styling is a few inline styles.
- `next.config.ts` sets the Turbopack root to `frontend/`, and sets
  `agentRules: false` so `next dev` does not write AI agent files.
- `.gitignore` ignores `.env*` except `.env.example`, and also the generated
  `next-env.d.ts`.
- The Vitest timeout is raised to 20 seconds so a busy machine does not fail
  passing tests.
