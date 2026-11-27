# Minimal RAG

A Next.js page sends a question to a FastAPI server. The server picks the most
relevant text from local files (BM25) and passes it to an LLM. By default the
LLM is a built-in stub, so no API key or network access is needed. An
OpenAI-compatible endpoint can be used instead. The full specification is in
[SPEC.md](SPEC.md).

## Requirements

- Node.js 26.13.1 (see `.nvmrc`; `nvm install` picks it up) with npm
- Python 3.14 with `venv`

## Setup

Run from the project root:

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
nvm use
npm ci --prefix frontend
```

`./test.sh` also does this setup when it is needed.

## Run

Terminal 1, the backend on port 8000:

```bash
backend/.venv/bin/python -m uvicorn --factory app.main:create_app \
  --app-dir backend --host 127.0.0.1 --port 8000
```

Terminal 2, the frontend:

```bash
nvm use
npm --prefix frontend run dev
```

Open the address that Next.js prints and ask, for example, "What grind size
should I use for espresso?". With no variables set, the answer starts with
`[stub]` and is followed by its sources. An unrelated question shows a fallback
answer and "No matching sources."

If port 8000 is taken, start the backend on another port and start the frontend
with `BACKEND_URL` set to match, such as `BACKEND_URL=http://127.0.0.1:8123`.

## Documents

The backend reads the `*.md` and `*.txt` files directly inside `data/` (no
subfolders) once, at startup. Restart it after changing them. Set `DOCS_DIR` to
use another folder; a relative path is resolved from the directory you start the
backend in.

Search matches words only: no stemming and no synonyms.

## Environment variables

| Name | Used by | Purpose |
|---|---|---|
| `DOCS_DIR` | backend | Documents folder |
| `LLM_PROXY_URL` | backend | Enables proxy mode; API base address, including any version path |
| `LLM_PROXY_API_KEY` | backend | Optional bearer key |
| `LLM_PROXY_MODEL` | backend | Model name; required in proxy mode |
| `BACKEND_URL` | frontend server | FastAPI base address (default: port 8000 on `127.0.0.1`) |
| `NEXT_TELEMETRY_DISABLED` | `test.sh` | Turns off Next.js telemetry |

The backend does **not** load `.env` files: export the variables in the shell
that starts it. `.env.example` lists the names only. Never commit secrets.

To use an OpenAI-compatible endpoint, export `LLM_PROXY_URL`, `LLM_PROXY_MODEL`
and, if it needs one, `LLM_PROXY_API_KEY`, then start the backend. The question
and the retrieved text are sent to that endpoint.

## Test

```bash
./test.sh
```

It installs dependencies if they are missing or their pinned files changed (only
that step may need the network), then runs the backend unit tests, the frontend
unit tests, a production build and the end-to-end tests. The end-to-end tests
start Uvicorn and `next start` on free loopback ports with a mock LLM, and stop
them afterwards. A `StarletteDeprecationWarning` about `httpx2` is expected.

To run one part after setup (with the proxy variables unset):

```bash
backend/.venv/bin/python -m pytest backend/tests
npm --prefix frontend test
npm --prefix frontend run build && backend/.venv/bin/python -m pytest e2e
```

## Choices made during the build

Points the specification left open:

- `.gitignore` ignores `.env*` but keeps `.env.example`, which holds names only.
  `next-env.d.ts` is ignored because Next.js generates it.
- `test.sh` stops if Node.js is not exactly the `.nvmrc` version, even after
  trying nvm. It records installs in `backend/.venv/requirements.txt` and
  `frontend/node_modules/.package-lock.stamp`.
- A leading UTF-8 byte order mark in a document is ignored.
- A heading line is one that starts with `#` after its block is trimmed.
- Empty or whitespace-only `LLM_PROXY_*` values count as unset, and
  surrounding whitespace is removed from them. An empty `DOCS_DIR` also counts
  as unset.
- A whitespace-only reply from the LLM counts as empty, so it is an error.
- A redirect from the LLM endpoint counts as a non-2xx failure.
- The UI sends the trimmed question.
- `next.config.ts` pins the Turbopack root to `frontend/` and sets
  `agentRules: false`, so `next dev` does not write an `AGENTS.md` file.
