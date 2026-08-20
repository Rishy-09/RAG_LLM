# Architecture diagram

```mermaid
flowchart TB
    %% Client boundary
    Client["API client\nSwagger UI / curl / application"]
    Key["Request header\nX-API-Key"]

    %% Deployment boundary
    subgraph Docker["Docker Compose network"]
        direction TB

        subgraph API["api container — FastAPI / Python"]
            direction TB
            Uvicorn["Uvicorn\nmain:app\nport 8000"]
            Main["main.py\nHTTP routes, auth, lifespan"]
            Auth["get_api_key()\nAPIKeyHeader validation"]
            State["app.state\nshared Qdrant client\nshared embedding client"]
            Temp["/app/_temp\ntemporary uploaded PDF"]

            subgraph Ingest["Ingestion service — embed.py"]
                direction TB
                Upload["POST /ingest\nmultipart file"]
                Save["Save uploaded bytes"]
                Loader["load_and_split_data()\nUnstructuredPDFLoader"]
                OCR["OCR fallback\npartition_pdf(hi_res)"]
                PDF["Parser fallback\nPyPDFLoader"]
                Splitter["RecursiveCharacterTextSplitter\nCHUNK_SIZE / CHUNK_OVERLAP"]
                Batch["embed_document()\nbatches of 100 chunks"]
                DocEmbed["OllamaEmbeddings\nembed_documents(texts)"]
                Points["Qdrant points\nUUID + vector + payload\ntext, source, page metadata"]
                Delete["finally: delete temp PDF"]
            end

            subgraph Query["Query service — query.py"]
                direction TB
                Ask["POST /query\nJSON: { prompt: ... }"]
                QueryEmbed["OllamaEmbeddings\nembed_query(prompt)"]
                Search["Nearest-neighbour search\nTOP_K / cosine distance"]
                Context["format_context_with_sources()\njoin retrieved chunk text"]
                Template["ChatPromptTemplate\ncontext + question"]
                Chain["prompt | ChatOllama | StrOutputParser"]
                Reply["JSON response\n{ response: answer }"]
            end

            Uvicorn --> Main
            Main --> Auth
            Main --> State
            Main --> Upload
            Main --> Ask
            Upload --> Save --> Temp
            Save --> Loader
            Loader -->|text returned| Splitter
            Loader -->|empty text| OCR --> Splitter
            Loader -->|exception| PDF --> Splitter
            Splitter --> Batch --> DocEmbed --> Points
            Points --> Delete
            Ask --> QueryEmbed --> Search --> Context --> Template --> Chain --> Reply
        end

        subgraph Ollama["ollama container — port 11434"]
            Startup["ollama_startup.sh\nserve, wait, pull models"]
            EmbedModel["nomic-embed-text\nembedding model"]
            LLM["llama3:instruct\nchat/generation model"]
            ModelVolume[("ollama_models volume\npersistent model files")]
            Startup --> EmbedModel
            Startup --> LLM
            EmbedModel --- ModelVolume
            LLM --- ModelVolume
        end
    end

    subgraph QdrantSystem["Qdrant — external to this Compose file"]
        Connect["get_vector_db.py\nconnect/retry/create collection"]
        Collection[("QDRANT_COLLECTION\nvector: EMBED_DIM\ndistance: cosine\npayload: text + metadata")]
        Connect --> Collection
    end

    Env[".env\nconfiguration and secret values"]

    Client -->|"HTTP :8000"| Uvicorn
    Client --> Key --> Auth
    Env -.->|"loaded / injected"| Main
    Env -.-> Connect
    Env -.-> Startup
    State --> Connect
    Points -->|"upsert vectors + payload"| Collection
    Search -->|"search vectors"| Collection
    DocEmbed -->|"HTTP :11434"| EmbedModel
    QueryEmbed -->|"HTTP :11434"| EmbedModel
    Chain -->|"HTTP :11434"| LLM
```

## How to read it

1. An API client reaches Uvicorn on port 8000 and provides `X-API-Key`.
2. `main.py` authenticates the request and uses resources created once during
   its lifespan: a Qdrant client and an Ollama embedding client.
3. `/ingest` follows the orange-left conceptual branch: save → extract PDF
   text → split → embed → upsert Qdrant points → delete the temporary PDF.
4. `/query` follows the blue-right conceptual branch: embed prompt → retrieve
   nearest chunks → construct context → ask the LLM → return text.
5. Ollama is local to the Compose network. Qdrant is not: its URL comes from
   `.env`, so it may be a local installation or Qdrant Cloud.

## Runtime startup sequence

```mermaid
sequenceDiagram
    participant Compose as Docker Compose
    participant Ollama as ollama container
    participant API as api / FastAPI
    participant Qdrant as Qdrant

    Compose->>Ollama: Start container
    Ollama->>Ollama: ollama serve
    Ollama->>Ollama: Pull embedding and chat models
    Ollama-->>Compose: Health check succeeds
    Compose->>API: Start api container
    API->>API: load_dotenv() and import modules
    API->>Qdrant: Connect (up to 3 attempts)
    alt collection does not exist
        API->>Qdrant: Create cosine collection (EMBED_DIM)
    end
    API->>API: Create shared embedding client
    API-->>Compose: Ready to accept requests on :8000
```

The diagrams use Mermaid, which renders automatically on GitHub and in most
modern Markdown viewers. If your editor does not render Mermaid, open this
file in GitHub or use its Markdown preview extension.
