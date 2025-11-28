import os
import logging
import shutil
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Security, Depends
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from dotenv import load_dotenv

from embed import embed_document
from query import query_rag_model
from get_vector_db import get_vector_db
from langchain_ollama import OllamaEmbeddings

load_dotenv()
# Set up logging for better error visibility in a production environment
logging.basicConfig(level=logging.INFO)

TEMP_DIR = os.getenv("TEMP_FOLDER", "./_temp")
API_KEY = os.getenv("API_KEY")
API_KEY_NAME = "X-API-Key"
TEXT_EMBEDDING_MODEL = os.getenv("TEXT_EMBEDDING_MODEL", "nomic-embed-text")


api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)


async def get_api_key(api_key: str = Security(api_key_header)):
    if api_key == API_KEY:
        return api_key
    else:
        raise HTTPException(status_code=403, detail="Could not validate credentials")
    

# Use asynccontextmanager to manage app lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    # This code runs at application startup
    logging.info("Application starting up...")
    os.makedirs(TEMP_DIR, exist_ok=True)
    app.state.qdrant_client = get_vector_db()
    app.state.embeding_model = OllamaEmbeddings(model=TEXT_EMBEDDING_MODEL) 
    logging.info("Shared resources (Qdrant client, Embedding model) initialized.")
    yield
    # This code runs at application shutdown
    logging.info("Application shutting down...")
    app.state.qdrant_client.close()

# with lifespan handler
app = FastAPI(
    title="Production-Ready RAG Agent", 
    version="2.0.0",
    lifespan=lifespan)

class QueryRequest(BaseModel):
    prompt: str

@app.post("/ingest", dependencies=[Depends(get_api_key)])
async def ingest_document(request: Request, file: UploadFile = File(...)):
    """
    Ingest a PDF document to be embedded into the vector database.
    """
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")
    
    temp_file_path = os.path.join(TEMP_DIR, file.filename)

    try:
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

            # Run the synchronous, blocking embed function in a separate thread
            await asyncio.to_thread(
                embed_document,
                flie_path = temp_file_path,
                qdrant_client = request.app.state.qdrant_client,
                embedding_model = request.app.state.embedding_model

            )
    except Exception as e:
        logging.error(f"An error occurred during file ingestion: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded files: {str(e)}")
 
    return {'message': f'Document {file.filename} ingested successfully.'}

@app.post('/query', dependencies=[Depends(get_api_key)])
async def query(query_request: QueryRequest, request=Request):
    """
    Query the RAG model with a prompt. Uses the shared clients for performance.
    """
    try:
        # Run the synchronous, blocking query function in a separate thread
        response = await asyncio.to_thread(
            query_rag_model,
            input_query=query_request.prompt,
            qdrant_client=request.app.state.qdrant_client,
            embedding_model=request.app.state.embedding_model
        )
        if not response:
            raise HTTPException(status_code=500, detail="Could not get a response.")
        return {"response": response}
    except Exception as e:
        logging.error(f"An error occurred during query: {e}")
        raise HTTPException(status_code=500, detail=f"An error occurred during query: {e}")
