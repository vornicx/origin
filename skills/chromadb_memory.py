"""ChromaDB como backend de memoria vectorial alternativa a pgvector.

Usa ChromaDB (embeddings locales en SQLite) como fallback ligero
cuando PostgreSQL + pgvector no están disponibles.

Reemplazo directo de la funcionalidad de búsqueda semántica
de MemoryManager sin requerir PostgreSQL.
"""

from typing import List, Dict, Any, Optional
import logging
import os

logger = logging.getLogger("origin.skills.chromadb")

CHROMA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "chroma")
COLLECTION_NAME = "origin_memories"


class ChromaMemoryBackend:
    """Backend de memoria vectorial usando ChromaDB.

    Uso:
        backend = ChromaMemoryBackend()
        backend.add("user said something", metadata={"type": "conversation"})
        results = backend.search("something")
    """

    def __init__(self, persist_dir: str = CHROMA_DIR):
        self._persist_dir = persist_dir
        self._collection = None
        self._ready = False
        self._init_chroma()

    def _init_chroma(self):
        try:
            import chromadb
            from chromadb.config import Settings

            os.makedirs(self._persist_dir, exist_ok=True)
            client = chromadb.PersistentClient(
                path=self._persist_dir,
                settings=Settings(anonymized_telemetry=False),
            )
            self._collection = client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            self._ready = True
            logger.info(f"ChromaDB ready at {self._persist_dir} ({self._collection.count()} docs)")
        except Exception as e:
            logger.warning(f"ChromaDB init failed: {e}")

    def add(self, text: str, metadata: Optional[Dict[str, Any]] = None, doc_id: Optional[str] = None):
        if not self._ready:
            return None
        import uuid

        doc_id = doc_id or str(uuid.uuid4())
        meta = dict(metadata) if metadata else {"source": "origin"}
        if "source" not in meta:
            meta["source"] = "origin"
        try:
            self._collection.add(
                documents=[text],
                metadatas=[meta],
                ids=[doc_id],
            )
            return doc_id
        except Exception as e:
            logger.warning(f"ChromaDB add failed: {e}")
            return None

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        if not self._ready:
            return []
        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=min(top_k, 50),
            )
            items = []
            ids = results.get("ids", [[]])[0]
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [None])[0] or []
            dists = results.get("distances", [None])[0] or []
            for i in range(len(ids)):
                items.append(
                    {
                        "id": ids[i],
                        "content": docs[i] if i < len(docs) else "",
                        "metadata": metas[i] if i < len(metas) and metas[i] else {},
                        "distance": dists[i] if i < len(dists) and dists[i] is not None else 0,
                    }
                )
            return items
        except Exception as e:
            logger.warning(f"ChromaDB search failed: {e}")
            return []

    def delete(self, doc_id: str):
        if not self._ready:
            return
        try:
            self._collection.delete(ids=[doc_id])
        except Exception as e:
            logger.warning(f"ChromaDB delete failed: {e}")

    def count(self) -> int:
        return self._collection.count() if self._ready else 0

    @property
    def is_ready(self) -> bool:
        return self._ready
