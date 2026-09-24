"""RAG indexing for the Draft Agent (Spec §5.5, §7.4).

The spec is chunked by section (pipeline.chunk_spec_by_section) and each chunk is embedded and
upserted into the platform `spec_embeddings` collection. Embedding sits behind a small interface so a
real provider (the harness memory server / Voyage) can be swapped in; when none is configured we fall
back to a deterministic hash-based 1024-dim vector so the pipeline never blocks and tests are stable.

The Atlas Vector Search index `spec_vector_idx` over `spec_embeddings.embedding` is a platform-lead
setup step; its definition is written to docs/atlas-vector-index.json.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
from typing import Any, Protocol

log = logging.getLogger(__name__)

EMBED_DIM = 1024
INDEX_NAME = "spec_vector_idx"


class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]:
        ...


class HashEmbedder:
    """Deterministic, dependency-free fallback: a unit-norm vector seeded from the text hash.

    Not semantically meaningful, but stable and non-zero so the index has real vectors to store and
    the code path is exercised end to end without a provider.
    """

    def __init__(self, dim: int = EMBED_DIM):
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        seed = hashlib.sha256((text or "").encode("utf-8")).digest()
        vals: list[float] = []
        counter = 0
        while len(vals) < self.dim:
            block = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            for i in range(0, len(block), 2):
                if len(vals) >= self.dim:
                    break
                n = int.from_bytes(block[i:i + 2], "big")
                vals.append((n / 65535.0) * 2.0 - 1.0)  # [-1, 1]
            counter += 1
        norm = math.sqrt(sum(v * v for v in vals)) or 1.0
        return [v / norm for v in vals]


def default_embedder() -> Embedder:
    """Return the configured embedder. Today only the deterministic fallback is wired; a provider can be
    selected via POC_EMBED_PROVIDER later (memory server / Voyage) without touching callers."""
    provider = os.environ.get("POC_EMBED_PROVIDER", "").lower()
    if provider in ("", "hash", "none"):
        return HashEmbedder()
    log.warning("unknown POC_EMBED_PROVIDER=%r; using deterministic hash embedder", provider)
    return HashEmbedder()


def _platform_db():
    from poc_shared_tools.config import get_config
    from pymongo import MongoClient
    cfg = get_config()
    if not cfg.platform_uri_set:
        raise RuntimeError("POC_PLATFORM_MONGODB_URI is not set")
    client = MongoClient(cfg.platform_mongodb_uri, serverSelectionTimeoutMS=10000)
    return client[cfg.platform_db]


def upsert_spec_embeddings(poc_id: str, spec_version: str, chunks: list[dict[str, str]],
                           owner_user_id: str, embedder: Embedder | None = None) -> int:
    """Embed each chunk and upsert into spec_embeddings keyed by (poc_id, spec_version, chunk_id).
    Returns the number of chunks written. Best-effort: callers should not fail the draft if this raises."""
    from datetime import datetime, timezone
    embedder = embedder or default_embedder()
    db = _platform_db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    written = 0
    for i, chunk in enumerate(chunks):
        chunk_id = f"{spec_version}-{i:03d}"
        doc = {
            "poc_id": poc_id,
            "spec_version": spec_version,
            "chunk_id": chunk_id,
            "section": chunk.get("section", ""),
            "text": chunk.get("text", ""),
            "embedding": embedder.embed(chunk.get("text", "")),
            "owner_user_id": owner_user_id,
            "updated_at": now,
        }
        db.spec_embeddings.update_one(
            {"poc_id": poc_id, "spec_version": spec_version, "chunk_id": chunk_id},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        written += 1
    return written


def atlas_index_definition() -> dict[str, Any]:
    """The Atlas Vector Search index the platform lead creates on spec_embeddings (setup step)."""
    return {
        "database": "poc_builder",
        "collection": "spec_embeddings",
        "name": INDEX_NAME,
        "type": "vectorSearch",
        "definition": {
            "fields": [
                {"type": "vector", "path": "embedding", "numDimensions": EMBED_DIM, "similarity": "cosine"},
                {"type": "filter", "path": "poc_id"},
                {"type": "filter", "path": "owner_user_id"},
                {"type": "filter", "path": "section"},
            ]
        },
    }
