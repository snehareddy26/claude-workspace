"""
Runtime RAG query script. Called by Claude via bash:
    python3 query_rag.py "design a rate limiter"

Returns top-K relevant chunks from the system design books.
"""

import os
import sys
import chromadb
from sentence_transformers import SentenceTransformer

# --- Config ---
BASE_DIR = os.path.expanduser("~/interview-assistant/rag")
CHROMA_DIR = os.path.join(BASE_DIR, "chroma_db")
COLLECTION_NAME = "sysdesign_books"
EMBED_MODEL = "all-MiniLM-L6-v2"
TOP_K = 6


def query(question: str) -> str:
    """Query ChromaDB for the most relevant chunks to the given question."""

    # Load persisted ChromaDB (fast — no re-embedding)
    client = chromadb.PersistentClient(path=CHROMA_DIR)

    try:
        collection = client.get_collection(COLLECTION_NAME)
    except Exception:
        return (
            "ERROR: ChromaDB collection not found. "
            "Please run ingest.py first:\n  python3 ~/interview-assistant/rag/ingest.py"
        )

    # Embed the question
    model = SentenceTransformer(EMBED_MODEL)
    question_embedding = model.encode([question])[0].tolist()

    # Find top-K most similar chunks
    results = collection.query(
        query_embeddings=[question_embedding],
        n_results=TOP_K,
        include=["documents", "metadatas", "distances"],
    )

    # Format output with source citations
    output_parts = []
    documents = results["documents"][0]
    metadatas = results["metadatas"][0]

    for i, (doc, meta) in enumerate(zip(documents, metadatas), 1):
        source = meta.get("source", "unknown")
        page_start = meta.get("page_start", "?")
        page_end = meta.get("page_end", "?")

        # Clean up source filename for display
        source_display = source.replace(".pdf", "").replace("_", " ")

        header = f"[{i}] Source: {source_display}, Pages {page_start}–{page_end}"
        output_parts.append(f"{header}\n{doc}")

    return "\n\n---\n\n".join(output_parts)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 query_rag.py \"your system design question\"")
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    print(query(question))
