"""
One-time PDF ingestion pipeline.
Run once after adding PDFs to rag/pdfs/:
    python3 ingest.py
"""

import os
import sys
import fitz  # pymupdf
import chromadb
from sentence_transformers import SentenceTransformer

# --- Config ---
BASE_DIR = os.path.expanduser("~/interview-assistant/rag")
PDF_DIR = os.path.join(BASE_DIR, "pdfs")
CHROMA_DIR = os.path.join(BASE_DIR, "chroma_db")
COLLECTION_NAME = "sysdesign_books"
CHUNK_SIZE = 400       # words per chunk
CHUNK_OVERLAP = 60     # words of overlap between consecutive chunks
EMBED_MODEL = "all-MiniLM-L6-v2"
BATCH_SIZE = 64        # embed this many chunks at once


def extract_pages(pdf_path: str) -> list[dict]:
    """Extract text from each page of a PDF, skipping blank/image-only pages."""
    doc = fitz.open(pdf_path)
    pages = []
    filename = os.path.basename(pdf_path)

    for page_num in range(len(doc)):
        text = doc[page_num].get_text("text")
        # Skip pages with very little text (diagrams, blank pages)
        if len(text.strip()) < 100:
            continue
        pages.append({
            "text": text.strip(),
            "source": filename,
            "page": page_num + 1,
        })

    doc.close()
    print(f"  Extracted {len(pages)} pages from {filename}")
    return pages


def chunk_pages(pages: list[dict]) -> list[dict]:
    """
    Sliding window chunking by word count.
    Each chunk: CHUNK_SIZE words with CHUNK_OVERLAP words of overlap.
    """
    chunks = []
    chunk_index = 0

    # Combine all pages into a flat list of (word, source, page)
    word_metadata = []
    for page in pages:
        words = page["text"].split()
        for word in words:
            word_metadata.append((word, page["source"], page["page"]))

    if not word_metadata:
        return chunks

    # Slide a window across the words
    step = CHUNK_SIZE - CHUNK_OVERLAP
    i = 0
    while i < len(word_metadata):
        window = word_metadata[i : i + CHUNK_SIZE]
        if not window:
            break

        chunk_words = [w for w, _, _ in window]
        chunk_text = " ".join(chunk_words)

        # Track which pages this chunk spans
        pages_in_chunk = list(dict.fromkeys(p for _, _, p in window))
        source = window[0][1]  # source file of first word

        chunks.append({
            "id": f"{source}_chunk_{chunk_index:04d}",
            "text": chunk_text,
            "source": source,
            "page_start": pages_in_chunk[0],
            "page_end": pages_in_chunk[-1],
            "chunk_index": chunk_index,
        })
        chunk_index += 1
        i += step

    return chunks


def ingest():
    """Main ingestion pipeline: extract → chunk → embed → store."""
    pdf_files = [f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf")]
    if not pdf_files:
        print(f"No PDFs found in {PDF_DIR}")
        sys.exit(1)

    print(f"Found {len(pdf_files)} PDF(s). Starting ingestion...\n")

    # --- Step 1: Extract all pages ---
    all_pages = []
    for pdf_file in sorted(pdf_files):
        pdf_path = os.path.join(PDF_DIR, pdf_file)
        all_pages.extend(extract_pages(pdf_path))

    print(f"\nTotal pages extracted: {len(all_pages)}")

    # --- Step 2: Chunk ---
    all_chunks = chunk_pages(all_pages)
    print(f"Total chunks created: {len(all_chunks)}\n")

    # --- Step 3: Embed ---
    print(f"Loading embedding model '{EMBED_MODEL}'...")
    model = SentenceTransformer(EMBED_MODEL)

    texts = [c["text"] for c in all_chunks]
    print(f"Embedding {len(texts)} chunks in batches of {BATCH_SIZE}...")

    embeddings = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        batch_embeddings = model.encode(batch, show_progress_bar=False)
        embeddings.extend(batch_embeddings.tolist())
        print(f"  Embedded {min(i + BATCH_SIZE, len(texts))}/{len(texts)} chunks", end="\r")

    print(f"\nEmbedding complete.")

    # --- Step 4: Store in ChromaDB ---
    print(f"Storing in ChromaDB at {CHROMA_DIR}...")
    client = chromadb.PersistentClient(path=CHROMA_DIR)

    # Delete existing collection if re-running to avoid duplicates
    try:
        client.delete_collection(COLLECTION_NAME)
        print("  Deleted existing collection (re-ingesting fresh).")
    except Exception:
        pass

    collection = client.create_collection(COLLECTION_NAME)

    # Upsert in batches (ChromaDB has a limit per call)
    STORE_BATCH = 100
    for i in range(0, len(all_chunks), STORE_BATCH):
        batch = all_chunks[i : i + STORE_BATCH]
        collection.add(
            ids=[c["id"] for c in batch],
            embeddings=embeddings[i : i + STORE_BATCH],
            documents=[c["text"] for c in batch],
            metadatas=[{
                "source": c["source"],
                "page_start": c["page_start"],
                "page_end": c["page_end"],
                "chunk_index": c["chunk_index"],
            } for c in batch],
        )

    print(f"Stored {len(all_chunks)} chunks in collection '{COLLECTION_NAME}'.")
    print("\nIngestion complete! You can now run query_rag.py.")


if __name__ == "__main__":
    ingest()
