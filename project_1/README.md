# Ask the docs (project_1: minimal RAG)

A Next.js page sends a question to a FastAPI server, which finds relevant text in
local files with BM25 and passes it to an LLM. The LLM is a built-in stub by
default; an OpenAI-compatible proxy is optional. See [SPEC.md](SPEC.md).

```
browser -> Next.js /api/ask -> FastAPI /ask -> BM25 (top 3) -> stub or LLM proxy
```

## Requirements

| Tool | Version |
|---|---|
| Node.js | 26.13.1 (`.nvmrc`; install with `nvm install`) |
| npm | 11.20.0 (tested version) |
| Python | 3.14.4, with `venv` (tested version) |

## Setup

Run all commands from this directory.

```bash
nvm use
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm ci --prefix frontend
```

`./test.sh` also does the last three steps when needed.

## Run

No API key or environment variables are needed. Use two terminals.

Terminal 1, the API:

```bash
backend/.venv/bin/python -m uvicorn app.main:create_app --factory \
  --app-dir backend --host 127.0.0.1 --port 8000
```

Terminal 2, the web page:

```bash
npm --prefix frontend run dev
```

Open the address Next.js prints (normally <http://localhost:3000>) and ask, for
example, **"What water temperature should I use to brew coffee?"** You should see
a `[stub]` answer and its sources. An unrelated question such as "Who won the
chess tournament?" shows the fallback answer and "No matching sources."

If port 8000 or 3000 is taken, choose others:

```bash
# Terminal 1: same uvicorn command with --port 8100
# Terminal 2:
BACKEND_URL=http://127.0.0.1:8100 npm --prefix frontend run dev -- --port 3100
```

For a production build:
`npm --prefix frontend run build && npm --prefix frontend start`.

## Documents

`*.md` and `*.txt` files directly inside `data/` (or `DOCS_DIR`) are read once,
when the API starts. Restart the API after changing them. Retrieval matches words
exactly: no synonyms or stemming ("bee" does not match "bees").

## Configuration

All variables are optional. [.env.example](.env.example) lists the names. The app
does not load a root `.env` file, so export the variables in the shell that starts
the process, for example `set -a; . ./.env; set +a`.

| Variable | Used by | Meaning |
|---|---|---|
| `DOCS_DIR` | API | Documents folder. Default: `data/` in this directory. A relative path is resolved from where you start the API |
| `BACKEND_URL` | web | API address. Default: `http://127.0.0.1:8000` |
| `LLM_PROXY_URL` | API | Base URL of an OpenAI-compatible API, including any version path such as `/v1`. When set, answers come from it |
| `LLM_PROXY_API_KEY` | API | Sent as a bearer token when set |
| `LLM_PROXY_MODEL` | API | Model name. Required when `LLM_PROXY_URL` is set |

In proxy mode, the question and the retrieved text are sent to that endpoint.

## API

- `GET /health` returns `{"status": "ok", "chunks": <count>}`.
- `POST /ask` with `{"question": "..."}` (1 to 1000 characters after trimming)
  returns `{"answer", "sources": [{"file", "text", "score"}], "mode"}`. It returns
  422 for an invalid question and 502 when the LLM request fails.

## Test

```bash
./test.sh
```

It installs dependencies only when they are missing or the pinned files changed
(only that step needs the network), then runs, stopping at the first failure:

1. backend unit tests (`backend/tests`, pytest)
2. frontend unit tests (`frontend/tests`, Vitest)
3. the Next.js production build
4. end-to-end tests (`e2e/`): real Uvicorn and `next start` processes on free
   loopback ports, with a mock LLM; the servers are stopped afterwards

No real LLM is called. pytest shows a `StarletteDeprecationWarning` suggesting
`httpx2`; this is expected with the pinned versions.

Not covered automatically: clicking through the page in a real browser, and a
real LLM endpoint.

## Choices made during the build

Points the specification left open:

- An empty `DOCS_DIR` or `BACKEND_URL` counts as unset, so a copied
  `.env.example` with blank values behaves like no configuration.
- A UTF-8 byte order mark at the start of a document is removed.
- The page sends the question already trimmed. "Thinking…" appears under the
  form while the button stays labelled "Ask".
- Text styling is inline; there is no stylesheet.
- `frontend/next.config.ts` sets the Turbopack root to `frontend/` and stops
  `next dev` from generating an `AGENTS.md` file.
- `.env.example` was added, and `.gitignore` also covers `.env` files and the
  generated `next-env.d.ts`.
- `test.sh` also turns off npm's update check so that it stays offline.
