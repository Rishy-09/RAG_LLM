import os
from qdrant_client import QdrantClient
from qdrant_client.http.models import VectorParams, Distance

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION")
EMBEDDING_DIMENSION =  int(os.getenv("EMBED_DIM", '768'))

def get_vector_db():
    client = QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY,
        timeout=120 # of 120 seconds
    )

    # if collection already exists!
    collections = client.get_collections()
    collections_names = [c.name for c in collections.collections]

    if QDRANT_COLLECTION not in collections_names:
        print(f'Collection {QDRANT_COLLECTION} not found. Creating it...')
        client.create_collection(
            collection_name = QDRANT_COLLECTION,
            vectors_config=VectorParams(size=EMBEDDING_DIMENSION, distance=Distance.COSINE)   
        )
        print(f'Collection {QDRANT_COLLECTION} created.')

    return client