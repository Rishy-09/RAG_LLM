# RAG Project — Improvement Notes

Quick-reference list of suggestions from code review. Organized by file, ordered by relevance (not line number).

---

## `main.py`

- **Path traversal risk** — `/ingest` uses `file.filename` directly in the save path. Sanitize with `os.path.basename(file.filename)` or a UUID-based name.
- **`.pdf` check is weak** — only checks the extension string, not actual file content/MIME type. A renamed file would pass.
- **API key comparison** — `api_key == API_KEY` is vulnerable to timing attacks. Use `secrets.compare_digest(api_key, API_KEY)`.
- **Missing `API_KEY` isn't validated at startup** — if unset, auth silently never matches anything. Consider raising at startup if missing.
- **`/ingest` error messages are imprecise** — same "Failed to save uploaded files" message covers both save AND embedding failures. Split into two try/except blocks for clarity.
- **Redundant exception wrapping in `/query`** — the `if not response: raise HTTPException(...)` gets caught and re-wrapped by the outer `except Exception`. Harmless but redundant.
- **Alternative worth considering**: swap raw `os.getenv()` config pattern for `pydantic-settings` (`BaseSettings`) — gives type validation + a single config object.
- **Alternative for lifespan**: current use of `@asynccontextmanager` is correct/modern — just noting the old `@app.on_event("startup")` style is deprecated, so no change needed here.

---

## `get_vector_db.py`

- **Dead code** — `last_error` variable is assigned but never used. Safe to delete.
- **`print()` inconsistency** — collection-creation messages use `print()` instead of `logging.info()`, unlike the rest of the file. Switch for consistency.
- **Linear backoff** (`2 * attempt` → 2s, 4s) — works, but exponential backoff (`2 ** attempt` → 2s, 4s, 8s) is more standard and matches what `query.py` already does. Make consistent across files.
- **Alternative**: could replace the manual retry loop with the `tenacity` library (`@retry(stop=stop_after_attempt(3), wait=wait_exponential(...))`) for less boilerplate.
- **Race condition (minor/edge case)** — if multiple app instances start simultaneously, two could both try `create_collection` at once. Only matters if you scale to multiple workers/replicas.
- **`EMBED_DIM` default (768)** — happens to match `nomic-embed-text`, but double-check it matches whatever embedding model you actually run.

---

## `embed.py`

- **Dead code — `allowed_file()`** — unused function; `main.py` already does its own `.pdf` check. Remove, or use this one instead since it's case-insensitive (`FILE.PDF` safe).
- **Dead code — `save_file()`** — entirely unused; looks like it was written for a different upload framework (`file.getbuffer()` isn't a FastAPI `UploadFile` method — likely leftover from a Streamlit prototype). Would also **not work correctly** if called as-is. Delete, or port its "sanitize + timestamp filename" idea into `main.py`'s actual `/ingest` save logic (this would also fix the path traversal issue above).
- **Dead import** — `time` is imported but never used.
- **Unused config** — `TEXT_EMBEDDING_MODEL` defined here but never referenced (model is passed in as a parameter instead).
- **Code duplication** — the `RecursiveCharacterTextSplitter` + `split_documents` call is repeated 3x across the loader fallback tiers. Could build `data` in each branch and split once at the end.
- **Non-idempotent ingestion** — every chunk gets a random `uuid.uuid4()` ID. Re-ingesting the same PDF twice creates duplicates instead of updating. Consider a deterministic ID (hash of `file_path + chunk_index`) if re-ingestion should overwrite.
- **Unused return value** — `embed_document` returns `True` on success, but `main.py` never checks it (only checks for exceptions). Harmless, just noting it's currently decorative.
- **Dependency note** — `secure_filename` pulls in `werkzeug` (a Flask dependency) into a FastAPI project. Works fine, but `python-slugify` or a custom regex would avoid the extra dependency if trimming requirements matters to you.

---

## `query.py`

- **Duplicate source citations (potential)** — the prompt instructs the LLM to write its own "Sources:" section, but `query_rag_model` *also* programmatically appends a sources list afterward. Could result in sources listed twice. Pick one approach — recommend relying only on the programmatic append, since it's more reliable than trusting LLM formatting.
- **`page_number` metadata likely missing** — `format_context_with_sources` looks for `payload['page_number']`, but `embed.py` never actually sets that key. Will mostly show "N/A". Check what key your PDF loader actually uses (e.g., `PyPDFLoader` typically uses `page`, not `page_number`) and match it — or check both: `.get('page', result.payload.get('page_number', 'N/A'))`.
- **Fragile "not found" detection** — sources are only appended if `"answer is not found"` isn't in the LLM's response text. Brittle — smaller/local models won't always phrase it exactly that way, so sources could get incorrectly appended to a "no answer" response. More robust: have the LLM return structured JSON (`{"answer": ..., "found": true/false}`) and branch on that instead of string matching.
- **`hasattr(qdrant_client, "search")` version-compatibility shim** — handles both old (`.search()`) and new (`.query_points()`) `qdrant-client` APIs. Works, but adds runtime complexity. Cleaner: pin your `qdrant-client` version in `requirements.txt` and just use `query_points()` directly.
- **Dead code** — commented-out `TEXT_EMBEDDING_MODEL` line. Delete.
- **`ChatOllama(model=LLM_MODEL)` created fresh per request** — unlike your Qdrant client / embedding model (both reused via `app.state`), a new LLM client is instantiated on every `/query` call. Likely low-cost, but for consistency, consider initializing once in `main.py`'s `lifespan` and passing it in.
- **400 vs 500 semantics** — empty `input_query` returns `None`, which `main.py` turns into a 500 error. Technically this is bad *input* (400/422), not a server error. Better fixed at the Pydantic model level in `main.py` (e.g., `prompt: str = Field(min_length=1)`).
- **Good pattern already in place** — exponential backoff retry (`2 ** attempt`) for the LLM call is correctly implemented here; use this as the reference when fixing `get_vector_db.py`'s backoff.

---

## Cross-file consistency checklist

- [ ] Standardize backoff strategy (linear vs exponential) — `query.py`'s exponential version is the better one to standardize on.
- [ ] Pick one source-citation approach (LLM-written vs. programmatic) — not both.
- [ ] Confirm actual metadata key names your PDF loaders produce (`page` vs `page_number`) and align `embed.py` output with `query.py` lookup.
- [ ] Remove all dead code: `allowed_file()`, `save_file()`, unused `time`/`TEXT_EMBEDDING_MODEL` imports, unused `last_error`.
- [ ] Swap remaining `print()` calls for `logging.info()`.
- [ ] Decide whether `ChatOllama` should move to shared `app.state` like the other clients.
- [ ] Sanitize uploaded filenames (path traversal fix) in `main.py`.
- [ ] Use `secrets.compare_digest()` for API key check.

---
