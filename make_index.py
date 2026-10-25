"""Rebuild an ID-mapped cosine index from summaries, never append duplicates."""

import hashlib
import json
import os

from config import ROOT, INDEX_PATH, MANIFEST_PATH, EMBED_MODEL
from init_db import connect
from progress import update, write_json, now


def summary_fingerprint(rows):
    return hashlib.sha256(json.dumps([(r["id"], r["summary"]) for r in rows],
                                    ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(2**20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    from runtime import unload
    update("make_index", detail="先卸載 Ollama 模型，再載入 bge-m3")
    unload()
    import numpy as np
    import torch
    import faiss
    from embeddings import load_encoder
    with connect() as db:
        rows = db.execute("SELECT id,summary FROM news WHERE NULLIF(TRIM(summary),'') IS NOT NULL ORDER BY id").fetchall()
        total = db.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    if not rows or len(rows) != total:
        raise RuntimeError("All articles must have summaries before indexing")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = load_encoder(device)
    index = None
    for start in range(0, len(rows), 8):
        batch = rows[start:start + 8]
        vectors = np.asarray(model.encode([r["summary"] for r in batch], batch_size=8,
                             normalize_embeddings=True, show_progress_bar=False), dtype="float32")
        if not np.isfinite(vectors).all():
            raise RuntimeError("Encoder produced non-finite vectors")
        if index is None:
            index = faiss.IndexIDMap(faiss.IndexFlatIP(vectors.shape[1]))
        index.add_with_ids(vectors, np.array([r["id"] for r in batch], dtype="int64"))
        update("make_index", min(start + 8, len(rows)), len(rows), "向量化新聞摘要", device=device)
    temporary = INDEX_PATH.with_suffix(".index.tmp")
    faiss.write_index(index, str(temporary))
    os.replace(temporary, INDEX_PATH)
    write_json(MANIFEST_PATH, {"model": EMBED_MODEL, "dimension": index.d, "vectors": index.ntotal,
               "normalized": True, "indexed_field": "summary", "summary_sha256": summary_fingerprint(rows),
               "index_sha256": file_sha256(INDEX_PATH), "created_at": now(), "device": device})
    print(f"Built {index.ntotal} unique summary vectors")


if __name__ == "__main__":
    main()
