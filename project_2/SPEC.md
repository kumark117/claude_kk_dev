# project_2: Minimal RAG, Specification

All paths are relative to the directory containing this file (the "project root").
Build only inside it.

## 1. Intent

A very small RAG app: a Next.js page sends a question to a FastAPI server, which
retrieves relevant text from local files and passes it to an LLM. The LLM is a
built-in stub by default, with an optional OpenAI-compatible proxy.

**Goals**

- Ask a question in a browser; see an answer and the sources behind it.
- Run and test with no API key and no real LLM.
- One command, `./test.sh`, runs every test.

**Non-goals:** document upload or editing in the UI, embeddings or a vector
database, chat history, streaming, authentication, retries, caching, Docker,
deployment, linting, a browser-driven test, hot reload of documents.

## 2. Approaches considered

| Decision | Chosen | Rejected | Reason |
|---|---|---|---|
| Document source | Files in a folder, read at startup | Paste in UI; file upload | No storage or upload handling |
| Retrieval | BM25 in plain Python | Local embedding model; remote embedding API | Offline, deterministic, no extra dependency. Does not match synonyms |
| LLM | Stub, plus optional OpenAI-compatible endpoint | Anthropic Messages API; stub only | Works with many servers and proxies |
| UI | Latest answer with sources | Answer only; chat history | Retrieval stays visible with little UI state |
| End-to-end test | Real Next.js and FastAPI processes over HTTP | Playwright browser; FastAPI in-process only | Checks the full server path without a browser download. UI clicks are covered by component tests |
| Browser to API | Next.js route handler forwards to FastAPI | Browser calls FastAPI with CORS | One origin, no CORS setup, backend address stays server-side |

## 3. Stack and exact versions

Use exact versions (no `^` or `~`).

### Toolchain (found on the build machine, Ubuntu 26.04 on WSL2)

| Tool | Version |
|---|---|
| Node.js | 26.13.1 (loaded through nvm) |
| npm | 11.20.0 |
| Python | 3.14.4, with `venv` |
| pip | 25.1.1 |

`uv`, Docker, `make` and `gcc` are not available. Do not depend on them.

### Frontend (`frontend/package.json`)

| Package | Version | Kind |
|---|---|---|
| next | 16.4.6 | dependency |
| react | 19.3.1 | dependency |
| react-dom | 19.3.1 | dependency |
| typescript | 5.9.3 | dev |
| @types/node | 26.6.11 | dev |
| @types/react | 19.3.3 | dev |
| @types/react-dom | 19.3.0 | dev |
| vitest | 5.1.2 | dev |
| vite | 8.4.0 | dev |
| @vitejs/plugin-react | 6.3.0 | dev |
| jsdom | 31.0.1 | dev |
| @testing-library/react | 16.3.3 | dev |
| @testing-library/dom | 10.4.2 | dev |

TypeScript stays on 5.9.3 even though newer majors exist. Commit
`package-lock.json`.

### Backend (`backend/requirements.txt`, complete pinned set)

```
annotated-doc==0.0.7
annotated-types==0.8.0
anyio==4.16.0
certifi==2026.11.20
click==8.5.1
fastapi==0.165.0
h11==0.16.0
httpcore==1.0.9
httpx==0.29.0
idna==3.20
iniconfig==2.3.1
opentelemetry-api==1.45.2
packaging==26.4
pluggy==1.7.0
pydantic==2.14.4
pydantic_core==2.50.5
Pygments==2.21.0
pytest==9.2.0
starlette==1.10.0
typing-inspection==0.4.5
typing_extensions==4.16.0
uvicorn==0.54.0
```

**What was verified:** both sets installed on this machine (Python from binary
wheels only). The backend packages and FastAPI's `TestClient` imported; the
`next`, `tsc` and `vitest` commands reported their versions and `jsdom` loaded.
No Next.js build or server was run.

Importing `TestClient` emits `StarletteDeprecationWarning` suggesting `httpx2`.
This is expected. Do not add `httpx2` or change versions to silence it.

## 4. Data flow

```
Browser (AskForm)
  │ POST /api/ask {question}
  ▼
Next.js route handler
  │ POST <backend>/ask {question}
  ▼
FastAPI
  ├─ retrieval: BM25 over in-memory chunks → top 3
  └─ llm: stub, or POST <proxy base>/chat/completions
  ▼
{answer, sources: [{file, text, score}], mode}
```

Documents are read and indexed once, when the backend starts.

## 5. File layout

```
README.md              how to set up, run and test
SPEC.md
test.sh                executable
pytest.ini             pythonpath = backend
.gitignore             .venv, node_modules, .next, __pycache__, .pytest_cache
.nvmrc                 26.13.1
data/                  two or three short sample .md files
backend/
  requirements.txt
  app/__init__.py
  app/main.py          app factory, settings, endpoints
  app/retrieval.py     loading, chunking, BM25
  app/llm.py           stub and proxy
  tests/test_retrieval.py
  tests/test_llm.py
  tests/test_api.py
frontend/
  package.json
  package-lock.json
  tsconfig.json
  next.config.ts
  vitest.config.mts
  app/layout.tsx
  app/page.tsx
  app/api/ask/route.ts
  components/AskForm.tsx
  lib/types.ts         response types shared by route and component
  tests/route.test.ts
  tests/AskForm.test.tsx
e2e/
  test_e2e.py
  mock_llm.py
  fixtures/docs/       small .md and .txt files with distinct topics
```

## 6. Components

### 6.1 Retrieval (`backend/app/retrieval.py`)

**Loading**

- Read `*.md` and `*.txt` directly inside the documents folder (no recursion),
  sorted by file name, as UTF-8.
- The folder is `DOCS_DIR`; when unset, it is `data/` at the project root,
  located relative to the source file, not the working directory.
- A missing folder or a file that is not valid UTF-8 stops startup with a clear
  error. An empty folder is allowed.

**Chunking**

- Normalize line endings and split on blank lines. Trim each block and drop
  empty ones.
- A block made only of Markdown heading lines (starting with `#`) is joined, with
  a newline, to the front of the next block. A heading with no following block is
  dropped.
- Split any chunk longer than 800 characters at whitespace into pieces of at
  most 800 characters. A single longer word is cut at 800.
- Each chunk keeps its file name and position.

**Tokenizing** (same for chunks and questions)

- Lowercase, then take runs of Unicode letters and digits (underscore and
  punctuation separate tokens). No stemming.
- Remove these stopwords: `a an and are as at be by can do does for from how i
  in is it of on or that the this to was what when where which who why with you`.

**Ranking** (BM25, `k1 = 1.5`, `b = 0.75`)

- `idf(t) = ln(1 + (N - n + 0.5) / (n + 0.5))`, with `N` chunks, `n` of them
  containing `t`.
- `score = Σ idf(t) · f · (k1 + 1) / (f + k1 · (1 - b + b · len / avglen))` over
  the unique question tokens; `f` is the count of `t` in the chunk, `len` its
  token count after stopword removal.
- Return at most 3 chunks with score above 0, ordered by score descending, then
  file name, then position.
- No chunks, or a question with no usable tokens, returns an empty list.

### 6.2 LLM (`backend/app/llm.py`)

One function takes the question and the retrieved chunks and returns the answer
text. The mode is chosen at startup: **proxy** if `LLM_PROXY_URL` is non-empty,
otherwise **stub**.

**Stub:** no network. Returns exactly
`[stub] Based on <file>: <text>` using the top chunk.

**Proxy**

- `LLM_PROXY_URL` is the API base, including any version path. Strip a trailing
  slash and POST to `<base>/chat/completions`.
- `LLM_PROXY_MODEL` is required in proxy mode; startup fails without it.
- Send `Authorization: Bearer <key>` only when `LLM_PROXY_API_KEY` is non-empty.
- Body: `model`, and `messages` holding (1) a system message telling the model to
  answer only from the supplied context and to say when the context is not
  enough, and (2) a user message with each chunk labelled by file name, followed
  by the question.
- Return `choices[0].message.content`.
- Timeout 30 seconds, no retries. A connection error, timeout, non-2xx status,
  invalid JSON, or missing or non-string or empty content raises one LLM error
  type.
- Never log or return the API key or the upstream response body.
- Accept an injectable `httpx` transport so tests can use `httpx.MockTransport`.

### 6.3 API (`backend/app/main.py`)

An app factory (`create_app`) builds the app from the environment, so tests can
create separate instances. Run it with Uvicorn's `--factory` option.

| Endpoint | Behavior |
|---|---|
| `GET /health` | 200 `{"status": "ok", "chunks": <count>}` |
| `POST /ask` | Body `{"question": string}` |

`POST /ask`:

- The question is trimmed and must then be 1 to 1000 characters; otherwise 422.
- 200 returns `{"answer": string, "sources": [{"file": string, "text": string,
  "score": number}], "mode": "stub" | "proxy"}`.
- With no matching chunks: answer is exactly
  `I couldn't find anything relevant in the documents.`, sources is empty, and
  the LLM is **not** called.
- An LLM error returns 502 `{"detail": "LLM request failed"}`.

### 6.4 Route handler (`frontend/app/api/ask/route.ts`)

- Invalid request JSON returns 400 `{"detail": "Invalid JSON"}`.
- Otherwise forward the body to `<backend>/ask`. The base is `BACKEND_URL`, read
  per request; when unset, use port 8000 on `127.0.0.1`.
- Pass through the backend's status and JSON body.
- A failed connection, a 35-second timeout, or a non-JSON reply returns 502
  `{"detail": "Backend unavailable"}`.

### 6.5 UI (`frontend/components/AskForm.tsx`, `app/page.tsx`)

- One page: a heading "Ask the docs", a labelled text input (max 1000
  characters), and an "Ask" button. Enter submits.
- The button is disabled while the trimmed input is empty or a request is in
  flight; show "Thinking…" while waiting.
- On success show the answer and, under "Sources", each file name, score (two
  decimals) and text. With none, show "No matching sources."
- On failure show the `detail` string if there is one, otherwise
  `Request failed (<status>)`. A network failure shows `Request failed`.
- A new submission clears the previous answer and error.
- Render all returned text as plain text. Minimal styling; no UI library and no
  remote fonts or assets.

### 6.6 README

Setup, how to start the backend (on port 8000) and the frontend in two
terminals, the environment variable names, and `./test.sh`.

## 7. Environment variables (names only)

| Name | Used by | Purpose |
|---|---|---|
| `DOCS_DIR` | backend | Documents folder |
| `LLM_PROXY_URL` | backend | Enables proxy mode; API base address |
| `LLM_PROXY_API_KEY` | backend | Optional bearer key |
| `LLM_PROXY_MODEL` | backend | Model name; required in proxy mode |
| `BACKEND_URL` | frontend server | FastAPI base address |
| `NEXT_TELEMETRY_DISABLED` | `test.sh` | Turns off Next.js telemetry |

The backend does not load `.env` files. Never commit secrets; `.gitignore` also
lists `.env*`.

## 8. Test plan

No test may reach an external network or a real LLM. Only loopback
(`127.0.0.1`) connections are allowed. Tests must not depend on the contents of
`data/`; use fixtures or temporary folders.

### 8.1 Backend unit tests (pytest)

- **Retrieval:** file type filtering and ordering; heading joining; long-chunk
  splitting; stopwords and punctuation; the relevant chunk ranks first; at most
  3 results; deterministic tie order; empty list for no match, stopword-only
  question and empty folder; startup errors for a missing folder and bad UTF-8.
- **LLM:** exact stub text; proxy request URL, headers, model and messages;
  no auth header without a key; each failure case raises the LLM error; mode
  selection; missing model fails startup. Use `httpx.MockTransport`.
- **API** (`TestClient`): health count; successful ask and response shape; 422
  for blank and over-long questions; no-match answer without calling the LLM;
  502 on LLM error without leaking the key or upstream body.

### 8.2 Frontend unit tests (Vitest)

- **Route** (node environment, `fetch` stubbed): invalid JSON; forwards to
  `BACKEND_URL`; passes through 200, 422 and 502; unreachable and non-JSON
  backend give 502.
- **AskForm** (jsdom, Testing Library, `fetch` stubbed): button disabled when
  empty and while loading; renders answer and sources; empty-sources message;
  string and non-string error details; network failure; old result cleared.

### 8.3 End-to-end test (`e2e/test_e2e.py`, pytest)

Start Uvicorn and `next start` (on the build made by `test.sh`) as subprocesses
on free loopback ports, with `DOCS_DIR` set to `e2e/fixtures/docs`. Wait for
readiness with a timeout, and always stop the whole process group afterwards. Use
`httpx` with `trust_env=False`. On startup failure, show the process output.

`mock_llm.py` is a standard-library HTTP server on a loopback port that records
requests and can be set to succeed or fail.

| Case | Expected |
|---|---|
| `GET /` | 200, page contains "Ask the docs" |
| Stub, matching question | 200, `mode` is `stub`, answer starts `[stub]`, first source is the expected fixture file |
| Stub, unrelated question | 200, fallback answer, no sources |
| Proxy, mock succeeds | Mock's canned answer returned, `mode` is `proxy`; mock received the path, model, dummy key and retrieved text |
| Proxy, mock returns 500 | 502, `LLM request failed` |

All requests in these cases go through Next.js `/api/ask`.

## 9. Test commands

The only command a user runs:

```
./test.sh
```

The build must create `test.sh`. It is a Bash script using `set -euo pipefail`
that changes to its own directory and stops at the first failure.

1. **Tools:** if `node` is not on `PATH` or is the wrong version, load nvm and
   select the `.nvmrc` version (nvm is not safe under `set -u`; relax it around
   the load). Fail with a clear message if `node` or `python3` is unavailable.
2. **Setup** (only when missing or when the pinned file changed, tracked by a
   copy of that file stored inside the installed folder):

   ```
   python3 -m venv backend/.venv
   backend/.venv/bin/python -m pip install -r backend/requirements.txt
   npm ci --prefix frontend
   ```

3. **Isolate:** unset `DOCS_DIR`, `BACKEND_URL`, `LLM_PROXY_URL`,
   `LLM_PROXY_API_KEY`, `LLM_PROXY_MODEL`, `HTTP_PROXY`, `HTTPS_PROXY`,
   `ALL_PROXY` and the lowercase proxy forms; disable Next.js telemetry.
4. **Test**, in this order:

   ```
   backend/.venv/bin/python -m pytest backend/tests
   npm --prefix frontend test
   npm --prefix frontend run build
   backend/.venv/bin/python -m pytest e2e
   ```

`frontend/package.json` scripts: `dev` (`next dev`), `build` (`next build`),
`start` (`next start`), `test` (`vitest run`).

Only first-time setup (step 2) may need network access. Once installed,
`./test.sh` must pass offline.

## 10. Acceptance criteria

1. From a fresh checkout, `./test.sh` installs dependencies, runs all four test
   commands and exits 0.
2. A second run with the network unavailable and no environment variables set
   also exits 0.
3. Any failing test makes `./test.sh` exit non-zero.
4. Following the README with no variables set, a question about the sample
   documents shows a `[stub]` answer and at least one source in the browser
   (manual check).
5. An unrelated question shows the fallback answer and "No matching sources."
6. With the proxy variables pointing at a compatible endpoint, the answer comes
   from that endpoint; verified automatically only against the mock.
7. If the backend is stopped, the page shows "Backend unavailable".
8. All direct and listed dependency versions match section 3, and no secrets are
   in the repository.

## 11. Left to the builder

Sample and fixture text, styling, internal function and type names, and any
Next.js config needed to build cleanly (for example, setting the Turbopack root
to `frontend/` if Next.js picks the wrong one).
