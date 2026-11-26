# Minimal RAG

Ask a question in the browser. A Next.js page sends it to a FastAPI server, which
finds relevant text in local files (BM25) and passes it to an LLM. The LLM is a
built-in stub by default, so no API key is needed. An OpenAI-compatible endpoint
can be used instead. See [SPEC.md](SPEC.md) for the full behaviour.

## Requirements

| Tool | Version |
|---|---|
| Node.js | 26.13.1 (see `.nvmrc`) |
| npm | 11.20.0 |
| Python | 3.14.4, with `venv` |

## Setup

Run from the project root:

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
npm --prefix frontend run dev
```

Open the address that Next.js prints (normally <http://localhost:3000>) and ask,
for example, "Why does Mars look red?". The page shows a `[stub]` answer and its
sources.

If port 8000 is taken, start the backend on another port and give the frontend
its address in `BACKEND_URL`. To change the frontend port, use
`npm --prefix frontend run dev -- --port <port>`.

For a production build, run `npm --prefix frontend run build` and then
`npm --prefix frontend run start`.

## Documents

The backend reads the `*.md` and `*.txt` files directly inside `data/`, or inside
`DOCS_DIR` if it is set (a relative path is relative to the directory the backend
is started from). Files are read once, so restart the backend after changing them.
Retrieval matches words, not meanings: there is no stemming or synonym matching.

## Environment variables

| Name | Used by | Purpose |
|---|---|---|
| `DOCS_DIR` | backend | Documents folder |
| `LLM_PROXY_URL` | backend | Enables proxy mode; API base address, including any version path |
| `LLM_PROXY_API_KEY` | backend | Optional bearer key |
| `LLM_PROXY_MODEL` | backend | Model name; required in proxy mode |
| `BACKEND_URL` | frontend server | FastAPI base address (default: port 8000 on `127.0.0.1`) |
| `NEXT_TELEMETRY_DISABLED` | `test.sh` | Turns off Next.js telemetry |

The backend does not load `.env` files: export the variables in the shell that
starts it. `.env.example` lists the names. Never commit secrets.

In proxy mode the backend sends the question and the retrieved text to
`<LLM_PROXY_URL>/chat/completions`.

## Test

```bash
./test.sh
```

It loads the right Node.js version through nvm if necessary, installs
dependencies when they are missing or their pinned files have changed (the only
step that needs the network), and runs, stopping at the first failure:

1. backend unit tests (pytest)
2. frontend unit tests (Vitest)
3. the Next.js production build
4. end-to-end tests: real Next.js and FastAPI processes on free loopback ports,
   with a mock LLM

No test uses a real LLM. A `StarletteDeprecationWarning` about `httpx2` is
expected.

Not covered automatically: clicking through the page in a real browser, and any
real LLM endpoint.

## Choices made while building

Points that SPEC.md leaves open:

- Node.js must be exactly the `.nvmrc` version, or `test.sh` stops.
- `.gitignore` ignores `.env*` but keeps `.env.example`. It also ignores the
  generated `next-env.d.ts`.
- A byte order mark at the start of a document is ignored.
- Heading joining applies to `.txt` files as well as `.md` files.
- The page sends the question with surrounding whitespace removed.
- `next.config.ts` sets the Turbopack root to `frontend/`, and `agentRules: false`
  so that `next dev` does not generate an `AGENTS.md` file.
- A 422 reply lists the error type and message but does not echo the input, so
  that text which cannot be encoded as UTF-8 still gets a 422.
- The page styles are a small inline block in `app/layout.tsx`.
