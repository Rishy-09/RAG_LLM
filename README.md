**Refactor** `embed.py`
This file is responsible for taking a document, chunking it, embedding the chunks, and storing them in the vector database.

- **Old Logic**: Your old code used db.`add_documents(chunks)`, which is a high-level LangChain command.

- **New Logic**: We will perform each of these steps manually to give you a clear understanding of what's happening.

        - First, call `get_vector_db()` to get your Qdrant client instance.

        - Then, use `OllamaEmbeddings` to generate a list of vectors from your document chunks.

        - Finally, use `client.upsert()` to push the vectors to your Qdrant collection, including the original text as metadata. This metadata is essential for the retrieval step.

--- 

**Refactor** `query.py`
This file is responsible for taking a user's query, finding relevant context, and generating a response.

- **Old Logic**: My old code used `MultiQueryRetriever`, which simplifies the retrieval process but hides the underlying mechanism.

- **New Logic**: We will explicitly handle the vector search and context extraction.

        - First, get your Qdrant client.

        - Then, embed the user's query.

        - Use `client.search()` to find the `top_k` documents. The results from Qdrant's search contain a payload field with the original text we stored in the `embed.py` step.

        - Extract this text and use it as the context for your LLM prompt.