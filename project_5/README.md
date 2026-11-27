# Minimal RAG

Ask a question in the browser and get an answer with the sources behind it. A
Next.js page sends the question to a FastAPI server, which ranks chunks of local
files with BM25 and passes the best three to an LLM. By default the LLM is a
built-in stub, so no API key and no network access are needed.

`SPEC.md` describes the exact behavior.

## Requirements

- Node.js 26.13.1 (see `.nvmrc`; load it with `nvm use`) and npm 11
- Python 3.14 with `venv` and pip

## Setup

From the project root:

```bash
nvm use
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm ci --prefix frontend
```

## Run

Use two terminals, both in the project root.

Terminal 1, the backend on port 8000:

```bash
backend/.venv/bin/python -m uvicorn app.main:create_app --factory \
  --app-dir backend --host 127.0.0.1 --port 8000
```

Terminal 2, the frontend:

```bash
nvm use
npm --prefix frontend run dev
```

Open the address that Next.js prints (normally <http://localhost:3000>) and ask,
for example, "How often should I check tyre pressure?". A question that matches
nothing, such as "Who won the chess final?", gets a fallback answer and "No
matching sources."

If port 8000 is taken, start the backend on another port and set `BACKEND_URL` in
the frontend terminal, for example `BACKEND_URL=http://127.0.0.1:8123`. To choose
the frontend port, append `-- --port 3123` to the `npm` command.

For a production build, use `npm --prefix frontend run build` and then
`npm --prefix frontend start`.

## Documents

The backend reads the `*.md` and `*.txt` files directly inside `data/` (or
`DOCS_DIR`), without recursion. Files must be UTF-8. They are read once at
startup, so restart the backend after changing them.

Search is by keyword: it does not match synonyms or different word forms
("tyre" does not match "tyres").

## Environment variables

All are optional unless noted. Export them in the shell that starts the process.
The backend does not load `.env` files. `.env.example` lists the names; never
commit real values.

| Name | Used by | Purpose |
|---|---|---|
| `DOCS_DIR` | backend | Documents folder |
| `LLM_PROXY_URL` | backend | Enables proxy mode; API base address, including any version path |
| `LLM_PROXY_API_KEY` | backend | Optional bearer key |
| `LLM_PROXY_MODEL` | backend | Model name; required in proxy mode |
| `BACKEND_URL` | frontend server | FastAPI base address (default: port 8000 on `127.0.0.1`) |
| `NEXT_TELEMETRY_DISABLED` | `test.sh` | Turns off Next.js telemetry |

With `LLM_PROXY_URL` set, the backend posts to `<url>/chat/completions` on an
OpenAI-compatible server. The question and the retrieved text are sent to that
server.

## Test

```bash
./test.sh
```

It loads the right Node.js version through nvm if needed, installs dependencies
when they are missing or the pinned files changed, and then runs:

1. backend unit tests (pytest)
2. frontend unit tests (Vitest)
3. the frontend production build
4. end-to-end tests: real Uvicorn and `next start` processes on free loopback
   ports, with a mock LLM server

Only the first-time installation needs network access. The tests use fixtures,
never `data/` or a real LLM. A `StarletteDeprecationWarning` suggesting `httpx2`
is expected.

## Choices made while building

Points that `SPEC.md` left open:

- An empty `DOCS_DIR` or `BACKEND_URL` counts as unset.
- A UTF-8 byte order mark at the start of a document is ignored.
- A reply from the LLM that consists only of whitespace is not treated as empty.
- `frontend/next.config.ts` sets the Turbopack root to `frontend/` and sets
  `agentRules: false`, so `next dev` does not write agent instruction files.
- `.gitignore` also covers generated files (`next-env.d.ts`, `*.tsbuildinfo`)
  and keeps `.env.example` tracked.
- `test.sh` also turns off the npm update check, to avoid network calls.
- The UI does not display `mode`; it is available in the API response.
