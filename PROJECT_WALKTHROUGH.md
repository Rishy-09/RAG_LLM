# How to read this project

This is a small Retrieval-Augmented Generation (RAG) API.  It accepts a PDF,
breaks it into text chunks, converts each chunk to a vector, saves the vectors
in Qdrant, and later uses the closest chunks as context for an Ollama model.

There are two distinct flows:

```text
Ingest: PDF -> temporary file -> PDF loader -> chunks -> embeddings -> Qdrant
Query:  prompt -> query embedding -> Qdrant similarity search -> context -> LLM -> response
```

The recommended reading order below follows both the system setup and the
actual runtime call paths. Read one small section, then trace the named
function into the next file before moving on.

## 1. Start with the project contract

Read these before reading Python:

1. `README.md` -- the intended product and API surface.
2. `.env.example` -- every setting the process expects.
3. `requirements.txt` -- the libraries that supply the non-standard classes.
4. `docker-compose.yml`, `Dockerfile`, and `ollama_startup.sh` -- how the
   service is run in containers.
5. `.gitignore` -- which local state and secrets should not be committed.

### Configuration map

| Variable | Read by | Why it matters |
| --- | --- | --- |
| `QDRANT_URL`, `QDRANT_API_KEY` | `get_vector_db.py` | Location and credentials of the vector database. |
| `QDRANT_COLLECTION` | all database operations | Logical collection that stores every document chunk. |
| `EMBED_DIM` | collection creation | Vector length. It must exactly match the embedding model output. |
| `OLLAMA_HOST` | LangChain/Ollama client indirectly | Address of the Ollama HTTP server. Compose overrides it to the `ollama` service. |
| `LLM_MODEL` | `query.py` | Generative model used to write the answer. |
| `TEXT_EMBEDDING_MODEL` | `main.py` | Model used for both document and query vectors. |
| `TEMP_FOLDER` | `main.py`, `embed.py` | Short-lived upload location. |
| `API_KEY` | `main.py` | Shared secret required by both endpoints. |
| `CHUNK_SIZE`, `CHUNK_OVERLAP` | `embed.py` | Controls chunk granularity and duplicated boundary text. |
| `TOP_K` | `query.py` | Number of nearest chunks sent to the LLM. |

`load_dotenv()` in `main.py` must execute before it imports the other local
modules, because those modules read configuration while they are imported.

## 2. Learn how the processes start

### `docker-compose.yml`

Compose starts two containers, not three:

* `ollama` exposes port 11434, persists downloaded models in `ollama_models`,
  and waits until its HTTP endpoint is healthy.
* `api` builds from the Dockerfile, exposes FastAPI on port 8000, receives
  values from `.env`, and uses `http://ollama:11434` because Docker services
  communicate by service name.

Qdrant is **not** started here. `QDRANT_URL` must point to an already-running
local or remote Qdrant instance.

### `Dockerfile`

The image starts from `python:3.11-slim`, installs OS packages needed by PDF
parsing/OCR, installs Python dependencies, copies the application, and runs
`uvicorn main:app`. The Compose `command` repeats that final command.

### `ollama_startup.sh`

The script backgrounds `ollama serve`, waits for HTTP readiness, pulls
`nomic-embed-text` and `llama3:instruct`, then waits on the server process.
The `wait` is essential: without it the shell exits and Docker stops the
container.

Alternatives:

* Pull models once during provisioning rather than every container start.
* Use a custom Ollama image with the models preloaded (larger image, faster
  start), or leave pulls to an operator.
* Add a Qdrant service to Compose for a fully local stack.

## 3. Read the API entry point: `main.py`

This file owns HTTP concerns; it should not know the details of parsing a PDF
or forming an LLM prompt.

### Imports and module configuration (lines 1-26)

* Lines 1-9 import standard helpers and FastAPI/LangChain types.
* Line 11 loads `.env` for local execution. In Docker, Compose also injects
  the same variables into the process environment.
* Lines 13-16 import the three application services after configuration is
  available.
* Lines 20-23 read configuration once at import time. Changing environment
  variables after import does not change these values.
* Line 26 declares a required `X-API-Key` request header. Header names are
  case-insensitive, so `x-api-key` works too.

### Authentication (lines 29-33)

`get_api_key` is a FastAPI dependency. `Security(api_key_header)` extracts the
header; the function compares it to `API_KEY`; a mismatch raises HTTP 403.
This is deliberately simple shared-secret authentication. Alternatives are
hashed keys stored server-side, OAuth/JWT, or placing the API behind an
identity-aware proxy.

### Lifespan (lines 37-48)

FastAPI runs the code before `yield` once at startup and the code after it on
shutdown. It creates the temp directory, initializes a single Qdrant client
and a single embedding-client object, saves both in `app.state`, then closes
Qdrant when the server stops. Sharing them avoids recreating clients per
request.

### Request model (lines 56-57)

`QueryRequest` makes FastAPI validate JSON. The actual request shape is:

```json
{"prompt": "What does the PDF say about pricing?"}
```

### Ingestion endpoint (lines 59-84)

`POST /ingest` requires the API-key dependency and a multipart `file` field.

1. Lines 64-65 reject a filename that does not end in lowercase `.pdf`.
2. Lines 67-71 write the uploaded stream to the temp directory.
3. Lines 74-79 run the synchronous embedding work in a worker thread. This
   keeps FastAPI's async event loop able to serve other requests while parsing,
   embedding, and database I/O occur.
4. `embed_document` receives the already-shared Qdrant and embedding clients.
5. Lines 80-82 translate any failure to HTTP 500; otherwise line 84 returns a
   success message.

### Query endpoint (lines 86-104)

`POST /query` validates `QueryRequest`, then runs `query_rag_model` in a worker
thread for the same reason. The return body is `{ "response": "..." }`.

## 4. Follow startup into `get_vector_db.py`

`get_vector_db()` is called by `main.lifespan`.

1. Lines 8-11 read database configuration and the expected vector dimension.
2. Lines 13-14 fail early if the minimum required location/collection is
   missing.
3. Lines 18-36 make at most three connection attempts. The sleeps are linear
   backoff (2 seconds, then 4 seconds). A production alternative is bounded
   exponential backoff with jitter.
4. Lines 39-47 list existing collections and create the configured one if it
   does not exist. `Distance.COSINE` ranks vectors by direction/similarity.
5. Line 49 returns the initialized client to the lifespan handler.

The collection schema is fixed when created. If `EMBED_DIM` disagrees with the
actual output dimension of `TEXT_EMBEDDING_MODEL`, Qdrant rejects upserts or
queries. Changing embedding model normally means creating a new collection and
re-ingesting documents.

## 5. Trace the ingestion pipeline in `embed.py`

Only `load_and_split_data` and `embed_document` participate in the current API
path. `allowed_file` and `save_file` are older helpers: they are not called by
`main.py`. Their safer, timestamped filename behavior is not currently used.

### Load and split (lines 39-66)

1. `UnstructuredPDFLoader` attempts rich PDF extraction (lines 43-44).
2. If it returns no non-whitespace text, `partition_pdf(..., strategy="hi_res")`
   attempts OCR and table-aware extraction (lines 47-51).
3. If Unstructured raises, `PyPDFLoader` provides a simpler fallback
   (lines 60-66).
4. `RecursiveCharacterTextSplitter` splits `Document` objects into chunks of
   up to `CHUNK_SIZE`, preserving `CHUNK_OVERLAP` characters between neighbours.
   Overlap helps avoid losing a sentence that crosses a boundary, at the cost
   of more vectors, storage, and repeated context.

Useful alternatives include token-based splitting (better aligned with model
limits), semantic splitting, splitting by headings/pages, or extracting page
and bounding-box metadata for citations.

### Embed and store (lines 70-116)

1. Lines 71-72 guarantee the input exists.
2. Line 75 obtains chunks; each 100-chunk batch is processed in lines 78-107.
3. Line 84 sends all batch texts to Ollama's embedding model, yielding one
   numeric vector per text.
4. Lines 87-99 create Qdrant points. Each has a random UUID, vector, and
   payload containing original chunk metadata plus `text` and a `source`.
5. Lines 102-106 upsert the points. `wait=True` means the call waits until
   Qdrant reports the write complete.
6. The `finally` block deletes the temporary upload whether ingestion succeeds
   or fails.

Because IDs are always new UUIDs, uploading the same PDF twice creates
duplicate chunks. A deterministic ID (for example, hash of document + page +
chunk) or a document ID with a delete-before-reindex workflow avoids that.

## 6. Trace the query pipeline in `query.py`

### Prompt and context helpers (lines 18-49)

`get_prompt_with_sources` creates a LangChain template with `{context}` and
`{question}` placeholders. `format_context_with_sources` extracts `text`,
`source`, and `page_number` from each Qdrant result, joins retrieved chunk text
with separators, and deduplicates source labels using a set.

### Retrieval and generation (lines 51-106)

1. Lines 56-57 reject an empty prompt.
2. Line 60 embeds the question with **the same embedding model** used for PDF
   chunks. This shared vector space is what makes similarity search meaningful.
3. Lines 63-77 request the `TOP_K` nearest vectors from Qdrant. The
   `search`/`query_points` branch supports different Qdrant client APIs.
4. Lines 79-80 return a fixed response when Qdrant finds no points.
5. Line 82 converts results into text context and source labels.
6. Lines 84-86 make a pipeline: fill template -> call Ollama chat model ->
   extract plain text.
7. Lines 90-99 retry an LLM request up to three times, sleeping 1 then 2
   seconds after failures.
8. Lines 102-104 append deterministic source labels unless the model says the
   exact phrase `answer is not found`.

The LLM is asked to print sources and the code also appends sources; this can
produce two source sections. More reliable citations would be built only by
code, using page metadata recorded at ingestion.

## 7. Confirm the real API, then experiment safely

Use the Swagger UI at `/docs` after startup. First upload a small text PDF to
`/ingest`; then send the returned knowledge base a `/query` request using the
`prompt` JSON field. Watch API logs and make a note of where the request moves:

```text
HTTP request
  -> main endpoint and auth
  -> worker thread
  -> embed.py or query.py
  -> Ollama and/or Qdrant
  -> main endpoint response
```

For learning, change one setting at a time: begin with `TOP_K`, then chunk
size/overlap, then prompt wording. Re-ingest into a separate collection when
changing the embedding model or its dimensions.

## Behavior gaps and improvement queue

These are observations about the current code, not features already present:

1. The README's query example sends `question`, but the API requires `prompt`.
2. The README promises CORS restrictions and a confidence score; neither is
   implemented. Retrieval scores are not requested or returned.
3. The README calls the system fully local, but Compose does not run Qdrant;
   the configured endpoint can be Qdrant Cloud.
4. There is no `.dockerignore`. `COPY . .` can place `.env` in the built image,
   even though `.gitignore` excludes it from Git. Add `.env`, `.venv`, `_temp`,
   and `__pycache__` to `.dockerignore`.
5. The upload path uses the client-supplied filename directly. A generated
   server-side name plus a stored display name is safer and prevents collisions.
6. Extension checking is case-sensitive and checks only the name, not the file
   content. Use a case-insensitive suffix check and content validation.
7. PDF page data is not normalized. The query code looks for `page_number`,
   but loaders may use a different key (often `page`), so citations can show
   `Page N/A`.
8. The project has no automated tests. The current Python modules compile, but
   compile success does not test Qdrant, Ollama, PDF parsing, authentication,
   or endpoint behavior.
