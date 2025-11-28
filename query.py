import os
import time
import logging
from langchain_ollama import ChatOllama
from langchain.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_ollama import OllamaEmbeddings
from qdrant_client import QdrantClient

# Set up logging for better visibility
logging.basicConfig(level=logging.INFO)

LLM_MODEL = os.getenv('LLM_MODEL', 'llama3:instruct')
TOP_K = int(os.getenv('TOP_K', 5))
# TEXT_EMBEDDING_MODEL = os.getenv('TEXT_EMBEDDING_MODEL', 'nomic-embed-text')
QDRANT_COLLECTION = os.getenv('QDRANT_COLLECTION')

def get_prompt_with_sources():
    """
    Creates a prompt template that instructs the LLM to answer based only on context
    and to cite the sources used.
    """
    template = """Answer the question based ONLY on the following context.
    If the context does not contain the answer, simply state that the answer is not found in the provided documents.
    After your answer, provide the sources you used in a 'Sources:' section. List each source on a new line.

    Context:
    {context}

    Question: {question}
    """
    return ChatPromptTemplate.from_template(template)

def format_context_with_sources(search_results):
    """
    Formats the search results into a single context string and a list of unique sources.
    """
    context_parts = []
    sources = set()
    for result in search_results:
        text = result.payload.get('text', '')
        source = result.payload.get('source', 'Unknown Source')
        page = result.payload.get('page_number', 'N/A')

        context_parts.append(text)
        sources.add(f"{source}, Page {page}")

    context = "\n\n---\n\n".join(context_parts)
    return context, sorted(list(sources))

def query_rag_model(input_query, qdrant_client: QdrantClient, embedding_model: OllamaEmbeddings):
    """
    Queries the RAG model by embedding the query, searching for relevant documents,
    and generating a response from an LLM.
    """ 
    if not input_query:
        return None

    logging.info(f"Embedding query: '{input_query[:50]}...'")
    query_vector = embedding_model.embed_query(input_query)

    logging.info(f"Searching for top {TOP_K} results in collection '{QDRANT_COLLECTION}'...")
    search_results = qdrant_client.search(
        collection_name=QDRANT_COLLECTION,
        query_vector=query_vector,
        limit=TOP_K,
        with_payload=True
    )

    if not search_results:
        return "I could not find any relevant information in the provided documents."

    context, sources = format_context_with_sources(search_results)
    
    prompt = get_prompt_with_sources()
    llm = ChatOllama(model=LLM_MODEL)
    chain = prompt | llm | StrOutputParser()

    logging.info("Invoking LLM for response generation...")
    llm_response = ""
    for attempt in range(3): # Simple retry logic for the LLM call
        try:
            llm_response = chain.invoke({'context': context, 'question': input_query})
            break
        except Exception as e:
            logging.warning(f"LLM call attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                raise
    
    # Append the sources to the final response
    if "answer is not found" not in llm_response.lower():
        sources_text = "\n\n**Sources:**\n- " + "\n- ".join(sources)
        llm_response += sources_text

    return llm_response