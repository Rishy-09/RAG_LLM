import os
import uuid
import time
import logging
from datetime import datetime
from werkzeug.utils import secure_filename
from langchain_community.document_loaders import PyPDFLoader, UnstructuredPDFLoader
from unstructured.partition.pdf import partition_pdf
from langchain.schema import Document
from qdrant_client import QdrantClient
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_ollama import OllamaEmbeddings

# Set up logging for better visibility
logging.basicConfig(level=logging.INFO)

QDRANT_COLLECTION = os.getenv('QDRANT_COLLECTION')
CHUNK_SIZE = int(os.getenv('CHUNK_SIZE', 1000))
CHUNK_OVERLAP = int(os.getenv('CHUNK_OVERLAP', 150))
BATCH_SIZE = 100

TEMP_FOLDER = os.getenv('TEMP_FOLDER', './_temp')
TEXT_EMBEDDING_MODEL = os.getenv('TEXT_EMBEDDING_MODEL', 'nomic-embed-text')


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'pdf'}

def save_file(file):
    os.makedirs(TEMP_FOLDER, exist_ok=True)
    ct = datetime.now()
    ts = ct.timestamp()
    filename = str(ts) + "_" + secure_filename(file.name)
    file_path = os.path.join(TEMP_FOLDER, filename)
    with open(file_path, 'wb') as f:
        f.write(file.getbuffer())
    return file_path

def load_and_split_data(file_path: str):
    logging.info(f"Attempting to load {file_path} with UnstructuredPDFLoader...")
    try:
        # Try with Unstructured first (OCR disabled by default unless needed)
        loader = UnstructuredPDFLoader(file_path=file_path)
        data = loader.load()

        # If Unstructured returns empty content, fallback to OCR
        if not any(doc.page_content.strip() for doc in data):
            logging.info("Unstructured returned empty content, falling back to OCR with partition_pdf...")
            elements = partition_pdf(filename=file_path, strategy="hi_res", infer_table_structure=True)
            # Create Document objects with basic metadata
            data = [Document(page_content=str(el), metadata={"source": os.path.basename(file_path)}) for el in elements]

        logging.info("Successfully loaded data.")
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP
        )
        return text_splitter.split_documents(data)

    except Exception as e:
        # If Unstructured itself crashes (like your unpacking error), fallback
        logging.warning(f"Unstructured loader failed ({e}), using PyPDFLoader as fallback.")
        loader = PyPDFLoader(file_path)
        data = loader.load()
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
        return text_splitter.split_documents(data)
    

# --- Main Embedding Logic (with Batching and Enhanced Metadata) ---
def embed_document(file_path: str, qdrant_client: QdrantClient, embedding_model: OllamaEmbeddings):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found at: {file_path}")

    try:
        chunks = load_and_split_data(file_path)
        logging.info(f"Generated {len(chunks)} chunks for embedding.")

        for i in range(0, len(chunks), BATCH_SIZE):
            batch_chunks = chunks[i:i + BATCH_SIZE]
            batch_texts = [chunk.page_content for chunk in batch_chunks]
            
            logging.info(f"Embedding batch {i//BATCH_SIZE + 1}...")
            # Use embed_documents for efficient batching
            vectors = embedding_model.embed_documents(batch_texts)
            
            points = []
            for j, chunk in enumerate(batch_chunks):
                # Use metadata from the chunk if available, otherwise create it
                metadata = chunk.metadata if hasattr(chunk, 'metadata') and chunk.metadata else {}
                metadata['text'] = chunk.page_content
                # Ensure source is always present
                if 'source' not in metadata or not metadata['source']:
                    metadata['source'] = os.path.basename(file_path)

                points.append({
                    'id': str(uuid.uuid4()),
                    'vector': vectors[j],
                    'payload': metadata
                })

            # Upsert points to Qdrant
            qdrant_client.upsert(
                collection_name=QDRANT_COLLECTION,
                points=points,
                wait=True # Ensure the operation is completed before proceeding
            )
            logging.info(f"Successfully upserted batch {i//BATCH_SIZE + 1}.")
        
        return True
    except Exception as e:
        logging.error(f"Failed during embedding process: {e}")
        raise e
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
            logging.info(f"Removed temporary file: {file_path}")