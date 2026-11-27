# Minimal RAG

Ask a question in the browser and get an answer plus the sources behind it. A
Next.js page forwards the question to a FastAPI server, which picks the most
relevant text from local files (BM25) and passes it to an LLM. The LLM is a
built-in stub by default, so no API key is needed. An OpenAI-compatible endpoint
can be used instead.

```
browser -> Next.js /api/ask -> FastAPI /ask -> BM25 (top 3) -> stub or proxy
```

## Requirements

| Tool | Version |
|---|---|
| Node.js | 26.13.1 (see `.nvmrc`) |
| npm | 11.20.0 |
| Python | 3.14.4, with `venv` |

## Setup

Run these from the project root. Network access is needed only here.

```bash
nvm use                      # or: nvm install
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm ci --prefix frontend
```

`./test.sh` also does this setup when it is missing.

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

Open the address that Next.js prints (usually <http://localhost:3000>) and ask,
for example, "How often should I check tyre pressure?". In stub mode the answer
starts with `[stub]` and repeats the best matching chunk. A question about
something the documents do not cover shows a fallback answer and
"No matching sources."

If port 8000 is taken, start the backend on another port and start the frontend
with `BACKEND_URL=http://127.0.0.1:<port>`. For another frontend port, use
`npm --prefix frontend run dev -- --port <port>`.

For a production build, run `npm --prefix frontend run build` and then
`npm --prefix frontend start`.

The backend can also be called directly:

| Endpoint | Result |
|---|---|
| `GET /health` | `{"status": "ok", "chunks": <count>}` |
| `POST /ask` with `{"question": "..."}` | `{"answer", "sources": [{"file", "text", "score"}], "mode"}` |

## Environment variables

All are optional unless noted. Export them in the shell that starts the process;
the backend does not load `.env` files. `.env.example` lists the names.

| Name | Used by | Purpose |
|---|---|---|
| `DOCS_DIR` | backend | Documents folder. Default: `data/` |
| `LLM_PROXY_URL` | backend | Enables proxy mode. API base address, including any version path (such as `/v1`) |
| `LLM_PROXY_API_KEY` | backend | Bearer key, if the endpoint needs one |
| `LLM_PROXY_MODEL` | backend | Model name. Required in proxy mode |
| `BACKEND_URL` | frontend server | FastAPI base address. Default: `http://127.0.0.1:8000` |
| `NEXT_TELEMETRY_DISABLED` | `test.sh` | Turns off Next.js telemetry |

In proxy mode the backend sends the question and the retrieved text to
`<LLM_PROXY_URL>/chat/completions`.

## Documents

The backend reads the `*.md` and `*.txt` files directly inside the documents
folder (UTF-8, no subfolders) once, at startup. Restart it after changing them.
Retrieval matches words, so synonyms and other word forms do not match.

## Test

```bash
./test.sh
```

It installs dependencies if they are missing or their pinned files changed, then
runs, stopping at the first failure:

1. backend unit tests (`backend/tests`)
2. frontend unit tests (Vitest, `frontend/tests`)
3. the Next.js production build
4. end-to-end tests (`e2e`), with real servers on free loopback ports and a mock
   LLM

After the first setup it runs offline. It needs no API key and calls no real LLM.
A `StarletteDeprecationWarning` suggesting `httpx2` is expected.

## Build notes

Choices made where `SPEC.md` left room:

- `.env.example` was added. `.gitignore` ignores `.env*` except that file, and
  also ignores the generated `next-env.d.ts` and `*.tsbuildinfo`.
- `frontend/app/globals.css` holds the minimal styling.
- `next.config.ts` sets the Turbopack root to `frontend/` and turns off the
  `AGENTS.md` file that `next dev` would otherwise generate.
- An empty `DOCS_DIR` or `BACKEND_URL` counts as unset.
- A UTF-8 byte order mark is ignored when documents are read.
- The page sends the question with surrounding spaces removed.
- `test.sh` also turns off the npm update check, which would use the network.
