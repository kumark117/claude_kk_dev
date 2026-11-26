# Minimal RAG

A Next.js page sends a question to a FastAPI server, which retrieves relevant
text from local files (BM25) and passes it to an LLM. The LLM is a built-in stub
by default, so no API key is needed. An OpenAI-compatible proxy is optional.

The full specification is in [SPEC.md](SPEC.md).

## Requirements

- Node.js 26.13.1 (see `.nvmrc`; with nvm: `nvm install` once, then `nvm use`)
- Python 3.14 with `venv`

## Setup

Run these from the project root. Only this step needs network access.

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm ci --prefix frontend
```

`./test.sh` does the same setup by itself when needed.

## Run

Use two terminals, both starting in the project root.

Terminal 1, the backend on port 8000:

```bash
backend/.venv/bin/python -m uvicorn app.main:create_app --factory --app-dir backend --port 8000
```

Terminal 2, the frontend:

```bash
nvm use
npm --prefix frontend run dev
```

Open the address that Next.js prints (usually <http://localhost:3000>) and ask,
for example, "How often should I water houseplants?". With no variables set, the
answer starts with `[stub]` and the sources are listed under it.

If port 8000 is taken, start the backend on another port and set `BACKEND_URL`
for the frontend, for example `BACKEND_URL=http://127.0.0.1:8100`.

## Documents

The backend reads the `*.md` and `*.txt` files directly inside `data/` (or the
folder named by `DOCS_DIR`). It reads them once, at startup: restart it after
changing them. Retrieval matches words, not meanings, so synonyms do not match.

## Environment variables

All are optional. [.env.example](.env.example) lists the names.

| Name | Used by | Purpose |
|---|---|---|
| `DOCS_DIR` | backend | Documents folder |
| `LLM_PROXY_URL` | backend | Enables proxy mode; API base address, including any version path |
| `LLM_PROXY_API_KEY` | backend | Optional bearer key |
| `LLM_PROXY_MODEL` | backend | Model name; required in proxy mode |
| `BACKEND_URL` | frontend server | FastAPI base address (default: port 8000 on `127.0.0.1`) |
| `NEXT_TELEMETRY_DISABLED` | `test.sh` | Turns off Next.js telemetry |

The backend does **not** load `.env` files: export its variables in the shell
that starts it. In proxy mode the backend posts to
`<LLM_PROXY_URL>/chat/completions`. Never commit keys.

## Test

```bash
./test.sh
```

It selects Node.js through nvm if needed, installs dependencies when they are
missing or their pinned files have changed, and then runs, stopping at the first
failure:

1. backend unit tests (pytest)
2. frontend unit tests (Vitest)
3. the frontend production build
4. the end-to-end test: real Uvicorn and `next start` processes on free loopback
   ports, with a mock LLM

After the first setup it works offline. It ignores any of the app and proxy
variables set in your shell.

`pytest` prints a `StarletteDeprecationWarning` about `httpx2`. This is expected.

## Choices made while building

Details that the specification left open:

- `.gitignore` ignores `.env*` but keeps `.env.example`. It also ignores
  Next.js and TypeScript generated files and the Vite cache.
- An empty `DOCS_DIR` or `BACKEND_URL` counts as unset.
- A byte order mark at the start of a document is accepted and removed.
- The heading and chunking rules apply to `.txt` files as well as `.md` files.
- A trailing slash on `BACKEND_URL` is ignored.
- The page sends the question with surrounding spaces already trimmed.
- `next.config.ts` sets the Turbopack root to `frontend/` and sets
  `agentRules: false`, so that `next dev` does not write `AGENTS.md` files.
- `test.sh` records installed versions as copies of `backend/requirements.txt`
  and `frontend/package-lock.json` inside `.venv` and `node_modules`.
