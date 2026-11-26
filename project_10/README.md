# Minimal RAG

Ask a question in the browser and get an answer with the sources behind it. A
Next.js page sends the question to a FastAPI server, which ranks chunks of local
files with BM25 and passes the top 3 to an LLM. The LLM is a built-in stub by
default, so no API key is needed. An OpenAI-compatible endpoint is optional.

See [SPEC.md](SPEC.md) for the full specification.

## Requirements

- Node.js 26.13.1 (see `.nvmrc`) with npm 11.20.0
- Python 3.14 with `venv`

## Setup

From the project root:

```bash
nvm use
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm ci --prefix frontend
```

`./test.sh` also performs this setup when needed.

## Run

Use two terminals, both in the project root.

Terminal 1, the backend on port 8000:

```bash
backend/.venv/bin/python -m uvicorn --factory app.main:create_app \
  --app-dir backend --host 127.0.0.1 --port 8000
```

Terminal 2, the frontend:

```bash
npm --prefix frontend run dev
```

Open the address that Next.js prints (normally <http://localhost:3000>; it picks
another port if that one is busy). Try "Which planet is the largest?" with the
sample documents. An unrelated question shows a fallback answer and "No matching
sources."

If port 8000 is taken, start the backend with another `--port` and start the
frontend with `BACKEND_URL=http://127.0.0.1:<port>`.

For a production build, run `npm --prefix frontend run build`, then
`npm --prefix frontend run start`.

## Documents

The backend reads the `*.md` and `*.txt` files directly inside `data/` (or
`DOCS_DIR`) once, at startup. Restart it after changing them. Retrieval matches
words, not meanings: there is no stemming or synonym matching.

## Environment variables

All are optional unless noted. `.env.example` lists the names.

| Name | Used by | Purpose |
|---|---|---|
| `DOCS_DIR` | backend | Documents folder (default: `data/`) |
| `LLM_PROXY_URL` | backend | Enables proxy mode; API base address, including any version path |
| `LLM_PROXY_API_KEY` | backend | Optional bearer key |
| `LLM_PROXY_MODEL` | backend | Model name; required in proxy mode |
| `BACKEND_URL` | frontend server | FastAPI base address (default: port 8000 on `127.0.0.1`) |
| `NEXT_TELEMETRY_DISABLED` | `test.sh` | Turns off Next.js telemetry |

The backend does not load `.env` files: export the variables in the shell that
starts it. For `BACKEND_URL`, Next.js also reads `frontend/.env.local`. Never
commit secrets.

In proxy mode the backend posts to `<LLM_PROXY_URL>/chat/completions`, sending
the question and the retrieved text to that endpoint.

## API

| Endpoint | Result |
|---|---|
| `GET /health` | `{"status": "ok", "chunks": <count>}` |
| `POST /ask` with `{"question": "..."}` | `{"answer", "sources": [{"file", "text", "score"}], "mode"}` |

A blank question or one over 1000 characters returns 422. A failed LLM request
returns 502.

## Test

```bash
./test.sh
```

It installs dependencies if they are missing or the pinned files changed, then
runs the backend tests, the frontend tests, the Next.js build and the end-to-end
tests, stopping at the first failure. Only the first-time installation needs
network access. Tests use loopback connections only, on free ports, with a mock
in place of a real LLM.

A `StarletteDeprecationWarning` about `httpx2` is expected.

## Choices made while building

The specification left these open:

- An empty `DOCS_DIR` or `BACKEND_URL` counts as unset.
- A UTF-8 byte order mark at the start of a document is ignored.
- The question is trimmed in the browser as well as in the backend.
- A successful reply with an unreadable body shows `Request failed (<status>)`.
- Styling is a few rules inside `frontend/app/layout.tsx`.
- `frontend/next.config.ts` sets the Turbopack root to `frontend/` and turns off
  the agent instruction files that `next dev` would otherwise write.
- `.env.example` is added and excluded from the `.env*` ignore rule.
- `.gitignore` also covers `.vite/`, `*.tsbuildinfo` and the generated
  `next-env.d.ts`.
- `test.sh` records installed versions in
  `backend/.venv/.requirements.txt` and
  `frontend/node_modules/.pinned-package-lock.json`.
