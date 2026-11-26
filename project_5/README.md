# Minimal RAG

Ask a question in the browser and get an answer with the sources behind it.

```
Browser -> Next.js /api/ask -> FastAPI /ask -> BM25 over local files -> LLM (stub or proxy)
```

No API key or real LLM is needed: the default LLM is a built-in stub.
`SPEC.md` is the full specification.

## Requirements

| Tool | Version |
|---|---|
| Node.js | 26.13.1 (see `.nvmrc`; `nvm use` selects it) |
| npm | 11.20.0 |
| Python | 3.14.4, with `venv` |

## Setup

From the project root:

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm ci --prefix frontend
```

`./test.sh` also does this when needed.

## Run

Use two terminals, both in the project root.

Terminal 1, backend on port 8000:

```bash
backend/.venv/bin/python -m uvicorn app.main:create_app --factory --app-dir backend --port 8000
```

Terminal 2, frontend:

```bash
npm --prefix frontend run dev
```

Open the address that Next.js prints (normally <http://localhost:3000>) and ask,
for example, "How does retrieval work?". A question about something the
documents do not mention gets a fallback answer and "No matching sources."

If port 8000 is taken, start the backend on another port and set `BACKEND_URL`
for the frontend. To move the frontend, add `-- --port <port>` to its command.

For a production build, run `npm --prefix frontend run build` and then
`npm --prefix frontend run start`.

## Documents

The backend reads `*.md` and `*.txt` files directly inside `data/` (no
subfolders), as UTF-8. Set `DOCS_DIR` to use another folder; a relative path is
relative to where you start the backend. Restart the backend after changing
documents.

Search is by keyword (BM25), so it does not match synonyms or word forms.

## Environment variables

All are optional unless noted. `.env.example` lists the names.

| Name | Used by | Purpose |
|---|---|---|
| `DOCS_DIR` | backend | Documents folder |
| `LLM_PROXY_URL` | backend | Enables proxy mode; API base address, including any version path |
| `LLM_PROXY_API_KEY` | backend | Bearer key, if the endpoint needs one |
| `LLM_PROXY_MODEL` | backend | Model name; **required** in proxy mode |
| `BACKEND_URL` | frontend server | FastAPI base address (default: port 8000 on `127.0.0.1`) |
| `NEXT_TELEMETRY_DISABLED` | `test.sh` | Turns off Next.js telemetry |

The backend does **not** load `.env` files: export its variables in the shell
that starts it. Next.js does read `.env*` files in `frontend/`. Never commit
secrets.

To use an OpenAI-compatible endpoint, export `LLM_PROXY_URL` and
`LLM_PROXY_MODEL` (and `LLM_PROXY_API_KEY` if needed), then start the backend.
Requests go to `<LLM_PROXY_URL>/chat/completions`.

## API

| Endpoint | Result |
|---|---|
| `GET /health` | `{"status": "ok", "chunks": <count>}` |
| `POST /ask` with `{"question": "..."}` | `{"answer", "sources": [{"file", "text", "score"}], "mode"}` |

Questions must be 1 to 1000 characters after trimming (otherwise 422). A failed
LLM request returns 502.

## Test

```bash
./test.sh
```

It installs missing or outdated dependencies, then runs the backend tests, the
frontend tests, a frontend build and the end-to-end tests, stopping at the first
failure. Only the installation needs network access. The tests use mocks and
loopback connections only, pick free ports and stop the servers they start.

A `StarletteDeprecationWarning` about `httpx2` is expected.

The browser UI is covered by component tests, not by a browser-driven test.
Check it by hand with the steps under "Run".

## Choices made while building

Where `SPEC.md` left something open:

- `.gitignore` ignores `.env*` but keeps `.env.example`. It also ignores the
  generated `next-env.d.ts` and `*.tsbuildinfo`.
- `next.config.ts` pins the Turbopack root to `frontend/` and sets
  `agentRules: false`, so `next dev` does not create `AGENTS.md`.
- A UTF-8 byte order mark at the start of a document is ignored.
- 422 responses list `type`, `loc` and `msg` but do not echo the input,
  because text with unpaired surrogates would otherwise cause a 500.
- A trailing slash on `BACKEND_URL` is ignored, as for `LLM_PROXY_URL`.
- A successful response that the page cannot parse shows `Request failed`.
- `test.sh` also turns off npm's update check, to stay offline.
- The end-to-end test starts `next start` through `node` directly, and runs one
  backend and frontend pair for each LLM mode.
- Vitest's test timeout is 20 seconds, to tolerate a busy machine.
