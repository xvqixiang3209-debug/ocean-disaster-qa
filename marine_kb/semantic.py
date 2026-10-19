"""Optional offline LSA index (TF-IDF + truncated SVD), not a neural embedding.

Only JSON and numeric NPZ are persisted; never unpickle an uploaded object.
The catalogue remains the authority on approval and identity at query time.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid


class LocalSemanticIndex:
    VERSION = 1
    METHOD = "local_tfidf_char_ngram_lsa"

    def __init__(self, database, index_dir):
        self.database = Path(database).resolve()
        self.index_dir = Path(index_dir).resolve()
        self._loaded_key = None
        self._cache = None
        self.last_status = "not_loaded"

    def _connect(self):
        db = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _fingerprint(db):
        checksum = hashlib.sha256()
        for row in db.execute("SELECT source_id,sha256,status,updated_at,chunk_count,metadata FROM sources ORDER BY source_id"):
            checksum.update(json.dumps(tuple(row), ensure_ascii=False).encode("utf-8"))
        # A mapping review changes retrieval scope even if the source is intact.
        for row in db.execute("SELECT chunk_id,sid,review_status FROM links ORDER BY chunk_id,sid"):
            checksum.update(json.dumps(tuple(row), ensure_ascii=False).encode("utf-8"))
        return checksum.hexdigest()

    def rebuild(self, *, dimensions=96, max_features=60000):
        """Explicit operator action; never invoked by a request or at startup."""
        import numpy as np
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.preprocessing import normalize
        from .retrieval import usable_source

        with closing(self._connect()) as db:
            db.execute("BEGIN")
            fingerprint = self._fingerprint(db)
            rows = []
            for raw in db.execute("SELECT c.chunk_id,c.text,s.filename,s.category,s.status,s.metadata FROM chunks c JOIN sources s USING(source_id) WHERE s.status IN ('indexed','approved') ORDER BY c.chunk_id"):
                row = dict(raw)
                row["metadata"] = json.loads(row["metadata"])
                row["title"] = row["filename"]
                if usable_source(row):
                    rows.append(row)
        if len(rows) < 3:
            self.last_status = "too_few_documents"
            return {"state": self.last_status, "indexed_chunks": len(rows), "method": self.METHOD}
        configuration = {"analyzer": "char", "ngram_range": (2, 4), "lowercase": True, "sublinear_tf": True, "norm": "l2", "max_features": min(max(1000, int(max_features)), 100000), "min_df": 1}
        vectorizer = TfidfVectorizer(**configuration, dtype=np.float32)
        matrix = vectorizer.fit_transform(row["filename"] + " " + row["text"] for row in rows)
        component_count = min(max(2, int(dimensions)), matrix.shape[0] - 1, matrix.shape[1] - 1, 256)
        if component_count < 2:
            self.last_status = "too_few_terms"
            return {"state": self.last_status, "method": self.METHOD}
        svd = TruncatedSVD(n_components=component_count, random_state=42, n_iter=5)
        vectors = normalize(svd.fit_transform(matrix), norm="l2").astype(np.float32)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        generation = uuid.uuid4().hex
        numeric_tmp = self.index_dir / f"vectors-{generation}.tmp.npz"
        manifest_tmp = self.index_dir / f"manifest-{generation}.tmp.json"
        numeric = self.index_dir / "vectors.npz"
        manifest = self.index_dir / "manifest.json"
        np.savez_compressed(numeric_tmp, vectors=vectors, components=svd.components_.astype(np.float32),
                            idf=vectorizer.idf_.astype(np.float32), chunk_ids=np.asarray([row["chunk_id"] for row in rows], dtype=np.int64))
        metadata = {"version": self.VERSION, "method": self.METHOD, "fingerprint": fingerprint,
                    "created_at": datetime.now(timezone.utc).isoformat(), "indexed_chunks": len(rows),
                    "dimensions": component_count, "features": len(vectorizer.vocabulary_),
                    "configuration": configuration, "vocabulary": {word: int(index) for word, index in vectorizer.vocabulary_.items()},
                    "vectors_sha256": hashlib.sha256(numeric_tmp.read_bytes()).hexdigest(),
                    "explained_variance_ratio": float(svd.explained_variance_ratio_.sum()),
                    "description": "本地字符TF-IDF与低秩LSA，不是深度语义embedding，不是DeepSeek微调"}
        manifest_tmp.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        os.replace(numeric_tmp, numeric)
        os.replace(manifest_tmp, manifest)
        self._loaded_key, self._cache = None, None
        self.last_status = "ready"
        return {key: value for key, value in metadata.items() if key not in {"vocabulary", "configuration"}} | {"state": "ready"}

    def _load(self, fingerprint):
        import numpy as np
        from sklearn.feature_extraction.text import TfidfVectorizer

        manifest = self.index_dir / "manifest.json"
        numeric = self.index_dir / "vectors.npz"
        if not manifest.is_file() or not numeric.is_file():
            self.last_status = "missing"
            return None
        if manifest.stat().st_size > 30_000_000 or numeric.stat().st_size > 2_000_000_000:
            self.last_status = "oversized_index"
            return None
        key = (manifest.stat().st_mtime_ns, numeric.stat().st_mtime_ns, fingerprint)
        if key == self._loaded_key:
            return self._cache
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        if meta.get("version") != self.VERSION or meta.get("method") != self.METHOD:
            self.last_status = "incompatible"
            return None
        if meta.get("fingerprint") != fingerprint:
            self.last_status = "stale"
            return None
        if hashlib.sha256(numeric.read_bytes()).hexdigest() != meta.get("vectors_sha256"):
            self.last_status = "integrity_failed"
            return None
        with np.load(numeric, allow_pickle=False) as data:
            arrays = {key: data[key] for key in ("vectors", "components", "idf", "chunk_ids")}
        if arrays["vectors"].ndim != 2 or arrays["components"].ndim != 2 or arrays["idf"].ndim != 1 or arrays["chunk_ids"].ndim != 1:
            raise ValueError("index_shape")
        if arrays["vectors"].shape != (len(arrays["chunk_ids"]), arrays["components"].shape[0]) or arrays["components"].shape[1] != len(arrays["idf"]):
            raise ValueError("index_shape")
        if any(not np.isfinite(value).all() for value in arrays.values()):
            raise ValueError("nonfinite_index")
        # Do not trust persisted class names, analyzer/callable, or paths.
        vocabulary = meta["vocabulary"]
        if len(vocabulary) != len(arrays["idf"]) or any(not isinstance(term, str) or not isinstance(index, int) or index < 0 or index >= len(vocabulary) for term, index in vocabulary.items()):
            raise ValueError("invalid_vocabulary")
        vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), lowercase=True, sublinear_tf=True, norm="l2", vocabulary=vocabulary, dtype=np.float32)
        vectorizer.idf_ = arrays["idf"]
        self._cache = (vectorizer, arrays)
        self._loaded_key = key
        self.last_status = "ready"
        return self._cache

    def search(self, query, limit=40, sid=None):
        try:
            import numpy as np
            from sklearn.preprocessing import normalize
            with closing(self._connect()) as db:
                loaded = self._load(self._fingerprint(db))
                if loaded is None:
                    return []
                vectorizer, arrays = loaded
                vector = normalize(vectorizer.transform([str(query)[:2000]]) @ arrays["components"].T, norm="l2")
                scores = (arrays["vectors"] @ vector[0]).ravel()
                if sid:
                    eligible = {row[0] for row in db.execute("SELECT DISTINCT chunk_id FROM links WHERE sid=? AND review_status IN ('auto_accepted','approved')", (sid,))}
                    scores = np.where(np.isin(arrays["chunk_ids"], list(eligible)), scores, -1.0)
                indices = np.argsort(-scores)[:max(1, min(int(limit), 100))]
                ordered = [(int(arrays["chunk_ids"][index]), float(scores[index])) for index in indices if float(scores[index]) > .05]
                if not ordered:
                    return []
                placeholders = ",".join("?" for _ in ordered)
                rows = db.execute(f"SELECT c.*,s.filename,s.category,s.status,s.metadata AS source_metadata FROM chunks c JOIN sources s USING(source_id) WHERE c.chunk_id IN ({placeholders}) AND s.status IN ('indexed','approved')", [identity for identity, _ in ordered]).fetchall()
                result = {}
                for raw in rows:
                    item = dict(raw)
                    item["metadata"] = {**json.loads(item.pop("source_metadata")), **json.loads(item["metadata"])}
                    item["title"] = item["filename"]
                    item["sids"] = [row[0] for row in db.execute("SELECT sid FROM links WHERE chunk_id=? AND review_status IN ('auto_accepted','approved')", (item["chunk_id"],))]
                    result[item["chunk_id"]] = item
                return [dict(result[identity], score=score, semantic_score=score, semantic_method=self.METHOD) for identity, score in ordered if identity in result]
        except (ImportError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            self.last_status = "unavailable:" + exc.__class__.__name__
            return []
