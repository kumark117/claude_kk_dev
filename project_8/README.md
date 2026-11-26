# Minimal RAG

A Next.js page sends a question to a FastAPI server. The server finds relevant
text in local files (BM25) and passes it to an LLM: a built-in stub by default,
or an OpenAI-compatible endpoint. The page shows the answer and its sources.
[SPEC.md](SPEC.md) is the full specification.

## Requirements

- Node.js 26.13.1 (see `.nvmrc`; `nvm use` selects it) and npm
- Python 3.14 with `venv`

## Setup

Run from the project root:

```bash
nvm use
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm ci --prefix frontend
```

`./test.sh` also does this setup when needed.

## Usage

Start the backend in one terminal:

```bash
backend/.venv/bin/python -m uvicorn app.main:create_app --factory \
  --app-dir backend --host 127.0.0.1 --port 8000
```

Start the frontend in a second terminal:

```bash
npm --prefix frontend run dev
```

Open the address that Next.js prints (normally <http://localhost:3000>) and ask
about the sample documents, for example "How often should I oil the chain?".
With no variables set, the answer starts with `[stub]`.

If port 8000 is taken, give the backend another `--port` and start the frontend
with `BACKEND_URL=http://127.0.0.1:<port>`. To choose the frontend port, append
`-- --port <port>` to the frontend command.

For a production build, use `npm --prefix frontend run build`, then
`npm --prefix frontend run start`.

### Your own documents

The backend reads the `*.md` and `*.txt` files directly inside `data/` (or
`DOCS_DIR`) as UTF-8. It reads them once, so restart it after changing them.
Retrieval matches words, not meanings: synonyms and other word forms do not
match.

### API

| Endpoint | Description |
|---|---|
| `GET /health` | `{"status": "ok", "chunks": <count>}` |
| `POST /ask` | Body `{"question": "..."}` (1 to 1000 characters after trimming); returns `answer`, `sources` and `mode` |

The browser calls `/api/ask` on the Next.js server, which forwards to `/ask`.

## Environment variables

| Name | Used by | Purpose |
|---|---|---|
| `DOCS_DIR` | backend | Documents folder |
| `LLM_PROXY_URL` | backend | Enables proxy mode; API base address including any version path |
| `LLM_PROXY_API_KEY` | backend | Optional bearer key |
| `LLM_PROXY_MODEL` | backend | Model name; required in proxy mode |
| `BACKEND_URL` | frontend server | FastAPI base address (default: port 8000 on `127.0.0.1`) |
| `NEXT_TELEMETRY_DISABLED` | `test.sh` | Turns off Next.js telemetry |

Set them in the shell that starts the process. The backend does not load `.env`
files, and `.env.example` only lists the names. Never commit secrets.

## Tests

```bash
./test.sh
```

It runs, in order, the backend tests (pytest), the frontend tests (Vitest), the
Next.js build and the end-to-end tests, and stops at the first failure. Only the
first-time dependency install needs network access. The tests use fixtures and a
mock LLM on loopback, and pick free ports.

A `StarletteDeprecationWarning` about `httpx2` is expected.

The browser checks in SPEC.md section 10 are manual.

## Build notes

Choices made where the specification left room:

- Sample documents (`data/`) cover coffee, bicycle care and houseplants. The
  end-to-end fixtures use different topics.
- `frontend/next.config.ts` sets the Turbopack root to `frontend/`.
- `.gitignore` also lists the generated `next-env.d.ts` and `*.tsbuildinfo`, and
  keeps `.env.example` tracked.
- Frontend test timeouts are raised so a busy machine does not cause spurious
  failures.
- The end-to-end test runs the installed `next` program with `node` (the same as
  `next start`, without an npm wrapper process).
- The 30-second proxy timeout is the `httpx` timeout, which applies to each
  network step (connect, read, write) rather than to the whole request.
- A trailing slash on `BACKEND_URL` is ignored.
