# project_6: Minimal RAG

A Next.js page sends a question to a FastAPI server. The server retrieves
relevant text from local files (BM25) and passes it to an LLM: a built-in stub
by default, or an OpenAI-compatible endpoint. No API key is needed to run or
test it. [SPEC.md](SPEC.md) is the full specification.

## Requirements

- Node.js 26.13.1 (see `.nvmrc`; `nvm install` picks it up) with npm 11
- Python 3.14 with `venv` and `pip`

## Setup

Run these from the project root:

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
for example, "How much coffee per litre of water?". With no variables set you
get a `[stub]` answer and its sources.

If port 8000 is taken, start the backend on another port and set `BACKEND_URL`
for the frontend. If port 3000 is taken, add `-- -p <port>` to the frontend
command.

For a production build, use `npm --prefix frontend run build`, then
`npm --prefix frontend run start`.

## Documents

The backend reads the `*.md` and `*.txt` files directly inside `data/` (or
`DOCS_DIR`) once, at startup. Restart it after changing them. Retrieval matches
words, not synonyms.

## Environment variables

All are optional unless noted. Names are also listed in `.env.example`.

| Name | Used by | Purpose |
|---|---|---|
| `DOCS_DIR` | backend | Documents folder (default: `data/`) |
| `LLM_PROXY_URL` | backend | Enables proxy mode; API base address, including any version path |
| `LLM_PROXY_API_KEY` | backend | Bearer key, if the endpoint needs one |
| `LLM_PROXY_MODEL` | backend | Model name; required in proxy mode |
| `BACKEND_URL` | frontend server | FastAPI base address (default: port 8000 on `127.0.0.1`) |
| `NEXT_TELEMETRY_DISABLED` | `test.sh` | Turns off Next.js telemetry |

Set them in the shell that starts the server. The backend does not load `.env`
files. Never commit secrets.

## Test

```bash
./test.sh
```

It installs missing or outdated dependencies (the only part that needs the
network), then runs the backend tests, the frontend tests, the frontend build
and the end-to-end tests, stopping at the first failure. Tests use mocks and
loopback connections only, pick free ports, and stop the servers they start.

## Choices made where the spec left room

- A variable that is set but empty counts as unset.
- A UTF-8 byte order mark at the start of a document is ignored.
- A question that matches nothing gets the fallback answer in either mode.
- The page sends the question with surrounding spaces trimmed.
- A success reply that the page cannot read shows `Request failed (<status>)`.
- A trailing slash on `BACKEND_URL` is ignored.
- `agentRules: false` in `frontend/next.config.ts` stops `next dev` from writing
  an `AGENTS.md` file.
- Styling is a small inline stylesheet in `frontend/app/layout.tsx`.
- `.gitignore` excludes `.env*` but keeps `.env.example`. It also excludes the
  generated `next-env.d.ts` and TypeScript build info.
- The end-to-end test runs two server pairs from one build: one in stub mode and
  one pointed at the mock LLM.
