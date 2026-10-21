"""Versioned SQLite catalogue with review gates and Chinese token search.

No user-supplied SQL or unrestricted filesystem path is accepted by this layer.
Raw files are immutable; review actions change eligibility, never original text.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import uuid

from .review_groups import classify_review_group, group_summary, review_group_id, review_progress, validate_review_group


def now():
    return datetime.now(timezone.utc).isoformat()


def tokens(text):
    result = []
    for word in re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", str(text).lower()):
        if re.fullmatch(r"[a-z0-9]+", word) or len(word) == 1:
            result.append(word)
        else:
            result.extend(word[i:i + 2] for i in range(len(word) - 1))
    return result


def dump(value):
    return json.dumps(value, ensure_ascii=False, default=str)


class KnowledgeStore:
    def __init__(self, database):
        self.database = Path(database).resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY, filename TEXT NOT NULL, relative_path TEXT NOT NULL,
                sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, raw_path TEXT NOT NULL,
                category TEXT NOT NULL, status TEXT NOT NULL, quality_status TEXT NOT NULL,
                metadata TEXT NOT NULL, warnings TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, chunk_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS source_hash ON sources(sha256);
            CREATE INDEX IF NOT EXISTS source_state ON sources(status, category);
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id INTEGER PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(source_id),
                locator TEXT NOT NULL, text TEXT NOT NULL, metadata TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS chunk_source ON chunks(source_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(title, body, tokenize='unicode61');
            CREATE TABLE IF NOT EXISTS links (
                chunk_id INTEGER NOT NULL REFERENCES chunks(chunk_id), sid TEXT NOT NULL,
                confidence REAL NOT NULL, review_status TEXT NOT NULL, method TEXT NOT NULL,
                evidence TEXT NOT NULL, PRIMARY KEY(chunk_id,sid)
            );
            CREATE INDEX IF NOT EXISTS link_sid ON links(sid,review_status);
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY, status TEXT NOT NULL, total INTEGER NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
                current_file TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL,
                results TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS review_log (
                id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, previous_status TEXT,
                status TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS capabilities (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS source_origin_confirmations (
                source_id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,confirmed_by TEXT NOT NULL,
                statement TEXT NOT NULL,confirmed_at TEXT NOT NULL,batch_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requirement_states (
                question_id TEXT PRIMARY KEY, status TEXT NOT NULL, note TEXT NOT NULL,
                actor TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requirement_state_log (
                id INTEGER PRIMARY KEY, question_id TEXT NOT NULL, previous_status TEXT NOT NULL,
                status TEXT NOT NULL, note TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
            );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=60)
        db.row_factory = sqlite3.Row
        db.create_function('review_group_id', 2, review_group_id, deterministic=True)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=60000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def source(row):
        item = dict(row)
        for key in ("metadata", "warnings"):
            item[key] = json.loads(item[key])
        # Disk locations are only used server-side, not exposed to browsers/LLM.
        item.pop("raw_path", None)
        item["title"] = item["filename"]
        item['review_group'] = classify_review_group(item['status'], item['metadata'], source_id=item['source_id'])
        item['review_progress'] = review_progress(item['status'], item['metadata'], source_id=item['source_id'])
        return item

    def get_source(self, source_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
            if row is None:
                return None
            result = self.source(row)
            confirmation = db.execute('SELECT confirmed_by,statement,confirmed_at FROM source_origin_confirmations WHERE source_id=? AND sha256=?',(source_id,row['sha256'])).fetchone()
            if confirmation:
                result['official_origin_confirmed'] = True
                result['origin_confirmation'] = dict(confirmation)
            return result

    def register(self, *, filename, relative_path, sha256, size, raw_path, category, metadata=None):
        identity = hashlib.sha256((relative_path + "\0" + sha256).encode()).hexdigest()[:24]
        with self.connect() as db:
            existing = db.execute("SELECT * FROM sources WHERE source_id=?", (identity,)).fetchone()
            if existing:
                return identity, False
            db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                identity, filename, relative_path, sha256, size, str(raw_path), category,
                "queued", "unprocessed", dump(metadata or {}), "[]", now(), now(), 0))
        return identity, True

    def finish(self, source_id, *, status, quality_status, metadata, warnings, chunks, linker=None):
        if len(chunks) > 30000:
            raise ValueError("单个来源切片超过资源上限")
        with self.connect() as db:
            source = db.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
            if not source:
                raise KeyError(source_id)
            # Retry replaces derived data transactionally, not raw source files.
            db.execute("DELETE FROM chunk_fts WHERE rowid IN (SELECT chunk_id FROM chunks WHERE source_id=?)", (source_id,))
            db.execute("DELETE FROM links WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE source_id=?)", (source_id,))
            db.execute("DELETE FROM chunks WHERE source_id=?", (source_id,))
            link_input = []
            for index, item in enumerate(chunks):
                meta = {**item.get("metadata", {}), "chunk_index": index}
                cur = db.execute("INSERT INTO chunks(source_id,locator,text,metadata) VALUES(?,?,?,?)", (
                    source_id, item["locator"], item["text"], dump(meta)))
                chunk_id = cur.lastrowid
                db.execute("INSERT INTO chunk_fts(rowid,title,body) VALUES(?,?,?)", (
                    chunk_id, " ".join(tokens(source["filename"])), " ".join(tokens(item["text"]))))
                link_input.append({"chunk_id": chunk_id, "document_id": source_id, "chunk_index": index,
                                   "text": item["text"], "title": source["filename"], "metadata": meta})
            if linker and link_input:
                for link in linker.link_chunks(link_input):
                    db.execute("INSERT OR IGNORE INTO links VALUES(?,?,?,?,?,?)", (
                        link.chunk_id, link.sid, link.confidence, link.review_status,
                        link.link_method, link.matched_text))
            db.execute("UPDATE sources SET status=?,quality_status=?,metadata=?,warnings=?,updated_at=?,chunk_count=? WHERE source_id=?", (
                status, quality_status, dump(metadata), dump(warnings), now(), len(chunks), source_id))

    def list_sources(self, *, q="", status="", category="", page=1, limit=40, review_group=""):
        review_group = validate_review_group(review_group)
        filters, params = [], []
        if q:
            filters.append("(filename LIKE ? ESCAPE '\\' OR relative_path LIKE ? ESCAPE '\\')")
            term = str(q).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.extend([f"%{term}%", f"%{term}%"])
        for field, value in (("status", status), ("category", category)):
            if value:
                filters.append(f"{field}=?")
                params.append(value)
        if review_group:
            filters.append('review_group_id(status,metadata)=?')
            params.append(review_group)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        page = max(1, int(page)); limit = min(100, max(1, int(limit)))
        with self.connect() as db:
            total = db.execute("SELECT count(*) FROM sources" + where, params).fetchone()[0]
            rows = db.execute("SELECT * FROM sources" + where + " ORDER BY created_at DESC, source_id LIMIT ? OFFSET ?", (*params, limit, (page - 1) * limit)).fetchall()
        return {"items": [self.source(r) for r in rows], "total": total, "page": page, "page_size": limit}

    def detail(self, source_id, *, limit=200):
        source = self.get_source(source_id)
        if not source:
            return None
        with self.connect() as db:
            chunks = [dict(r) for r in db.execute("SELECT * FROM chunks WHERE source_id=? ORDER BY chunk_id LIMIT ?", (source_id, limit))]
            for item in chunks:
                item["metadata"] = json.loads(item["metadata"])
            links = [dict(r) for r in db.execute("SELECT l.* FROM links l JOIN chunks c USING(chunk_id) WHERE c.source_id=? ORDER BY l.confidence DESC LIMIT 500", (source_id,))]
            reviews = [dict(r) for r in db.execute("SELECT previous_status,status,note,created_at FROM review_log WHERE source_id=? ORDER BY id DESC LIMIT 100", (source_id,))]
        return {"source": source, "chunks": chunks, "links": links,
                "review_history": reviews,
                "chunks_truncated": source["chunk_count"] > limit}

    def review(self, source_id, status, note, external_allowed=None):
        if status not in {"approved", "rejected", "pending_review"}:
            raise ValueError("审核状态无效")
        if len(note.strip()) < 4:
            raise ValueError("请填写至少4字的审核依据或原因")
        with self.connect() as db:
            source = db.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
            if not source:
                raise KeyError(source_id)
            if status == "approved" and (not source["chunk_count"] or source["quality_status"] not in {"parsed", "partial"}):
                raise ValueError("无可用正文或结构化元数据未确认，不能发布为正文知识")
            metadata = json.loads(source["metadata"])
            if status == "approved" and metadata.get("is_draft"):
                raise ValueError("草案需补交正式文件；不可仅改状态升级")
            if status == "approved" and metadata.get("requires_human_review") and not note.startswith("已逐页核对"):
                raise ValueError("OCR/缺页资料须核验后，以‘已逐页核对’开头记录核验范围和依据")
            if external_allowed is not None:
                if not isinstance(external_allowed, bool):
                    raise ValueError("外发授权必须是布尔值")
                metadata["external_allowed"] = external_allowed
                metadata['external_permission_override'] = external_allowed
            metadata["review_note"] = note.strip()[:2000]
            db.execute("UPDATE sources SET status=?,metadata=?,updated_at=? WHERE source_id=?", (status, dump(metadata), now(), source_id))
            db.execute("INSERT INTO review_log(source_id,previous_status,status,note,created_at) VALUES(?,?,?,?,?)", (source_id, source["status"], status, note[:2000], now()))
        return self.get_source(source_id)

    def search_candidates(self, query, limit=80, sid=None, include_pending=False):
        words = list(dict.fromkeys(tokens(str(query)[:2000])))[:100]
        if not words:
            return []
        expression = " OR ".join('"' + w.replace('"', '""') + '"' for w in words)
        states = "('approved','indexed','pending_review')" if include_pending else "('approved','indexed')"
        params = [expression]
        clause = ""
        if sid:
            clause = " AND EXISTS(SELECT 1 FROM links l WHERE l.chunk_id=c.chunk_id AND l.sid=? AND l.review_status IN ('auto_accepted','approved'))"
            params.append(sid)
        params.append(min(200, max(1, int(limit))))
        with self.connect() as db:
            rows = db.execute(f"""SELECT c.*,s.filename,s.category,s.status,s.metadata AS source_metadata,
                -bm25(chunk_fts,1.4,1.0) AS score FROM chunk_fts
                JOIN chunks c ON c.chunk_id=chunk_fts.rowid JOIN sources s USING(source_id)
                WHERE chunk_fts MATCH ? AND s.status IN {states} {clause}
                ORDER BY bm25(chunk_fts,1.4,1.0) LIMIT ?""", params).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["metadata"] = {**json.loads(item.pop("source_metadata")), **json.loads(item["metadata"])}
                item["title"] = item["filename"]
                item["sids"] = [r[0] for r in db.execute("SELECT sid FROM links WHERE chunk_id=? AND review_status IN ('auto_accepted','approved')", (item["chunk_id"],))]
                item["url"] = "/knowledge.html?source=" + item["source_id"]
                result.append(item)
        return result

    def named_document_candidates(self, query, limit=80, sid=None, include_pending=False):
        """Search inside documents explicitly named by the user first.

        A generic BM25 top-80 can be crowded by annual reports sharing an
        edition year. This adds candidates; the ordinary approval, version,
        relevance and external-permission gates still run afterwards.
        """
        import unicodedata
        from .document_identity import notice_codes
        text = unicodedata.normalize('NFKC', str(query))
        names = [x for x in re.findall(r'《([^》]{4,100})》', text)[:3]]
        codes = re.findall(r'(?:GB|HY|DB\d+)\s*[/∕_ -]?\s*T?\s*(\d+(?:\.\d+)?)\s*[-—－]\s*(\d{4})', text, re.I)[:3]
        notices = notice_codes(text)[:3]
        clauses, parameters = [], []
        for name in names:
            clauses.append('instr(s.filename,?)>0'); parameters.append(name)
        for number, year in codes:
            clauses.append('(instr(s.filename,?)>0 AND instr(s.filename,?)>0)'); parameters.extend((number, year))
        for issuer, year, issue in notices:
            # Normalize punctuation and spacing after this bounded SQL scan;
            # a different year or issue number is never the named document.
            clauses.append('instr(s.filename,?)>0'); parameters.append(issuer)
        if not clauses:
            return []
        states = "('approved','indexed','pending_review')" if include_pending else "('approved','indexed')"
        sid_filter = " AND EXISTS(SELECT 1 FROM links l WHERE l.chunk_id=c.chunk_id AND l.sid=? AND l.review_status IN ('auto_accepted','approved'))" if sid else ''
        params = list(parameters) + ([sid] if sid else [])
        with self.connect() as db:
            rows = db.execute(f'''SELECT c.*,s.filename,s.category,s.status,s.metadata AS source_metadata
                FROM chunks c JOIN sources s USING(source_id)
                WHERE ({' OR '.join(clauses)}) AND s.status IN {states}{sid_filter}
                ORDER BY c.chunk_id LIMIT 3000''', params).fetchall()
            words = set(tokens(text))
            results = []
            for raw in rows:
                item = dict(raw)
                filename = unicodedata.normalize('NFKC', item['filename'])
                ordinary_match = (any(name in filename for name in names) or
                                  any(number in filename and year in filename for number, year in codes))
                if notices and not ordinary_match and not set(notices) & set(notice_codes(filename)):
                    continue
                item['metadata'] = {**json.loads(item.pop('source_metadata')), **json.loads(item['metadata'])}
                item['title'] = item['filename']
                item['sids'] = [r[0] for r in db.execute("SELECT sid FROM links WHERE chunk_id=? AND review_status IN ('auto_accepted','approved')", (item['chunk_id'],))]
                item['url'] = '/knowledge.html?source=' + item['source_id']
                item['named_document_match'] = True
                item['score'] = len(words & set(tokens(item['text'])))
                results.append(item)
        results.sort(key=lambda x: (-x['score'], x['chunk_id']))
        return results[:min(200, max(1, int(limit)))]

    def review_link(self, chunk_id, sid, status, note):
        if status not in {'approved','rejected','needs_review'} or len(note.strip()) < 4:
            raise ValueError('关联审核需有效状态及至少4字依据')
        with self.connect() as db:
            row = db.execute('SELECT c.source_id,l.review_status FROM links l JOIN chunks c USING(chunk_id) WHERE l.chunk_id=? AND l.sid=?', (chunk_id,sid)).fetchone()
            if not row:
                raise KeyError('关联候选不存在')
            db.execute('UPDATE links SET review_status=? WHERE chunk_id=? AND sid=?', (status,chunk_id,sid))
            db.execute('INSERT INTO review_log(source_id,previous_status,status,note,created_at) VALUES(?,?,?,?,?)', (row['source_id'],row['review_status'],'link:'+status, f'{chunk_id}/{sid}: {note}',now()))
        return {'chunk_id':chunk_id,'sid':sid,'review_status':status}

    def requirement_states(self):
        with self.connect() as db:
            rows = db.execute("SELECT question_id,status,note,actor,updated_at FROM requirement_states").fetchall()
        return {row["question_id"]: {"status": row["status"], "status_note": row["note"],
                "status_actor": row["actor"], "status_updated_at": row["updated_at"]} for row in rows}

    def update_requirement_state(self, question_id, status, note, actor=""):
        from .requirements import QUESTION_STATES
        question_id = str(question_id).strip()[:40]
        note = str(note or "").strip()[:2000]
        actor = str(actor or "本机工作台用户").strip()[:80]
        if not question_id or status not in QUESTION_STATES:
            raise ValueError("题目编号或状态无效")
        if len(note) < 4:
            raise ValueError("请填写至少4字的核定依据/缺项说明")
        with self.connect() as db:
            row = db.execute("SELECT status FROM requirement_states WHERE question_id=?", (question_id,)).fetchone()
            previous = row["status"] if row else "pending_confirmation"
            db.execute("INSERT INTO requirement_states(question_id,status,note,actor,updated_at) VALUES(?,?,?,?,?) "
                       "ON CONFLICT(question_id) DO UPDATE SET status=excluded.status,note=excluded.note,actor=excluded.actor,updated_at=excluded.updated_at",
                       (question_id, status, note, actor, now()))
            db.execute("INSERT INTO requirement_state_log(question_id,previous_status,status,note,actor,created_at) VALUES(?,?,?,?,?,?)",
                       (question_id, previous, status, note, actor, now()))
        return self.requirement_states()[question_id]

    def status(self):
        with self.connect() as db:
            counts = lambda field: dict(db.execute(f"SELECT {field},count(*) FROM sources GROUP BY {field}").fetchall())
            result = {"source_count": db.execute("SELECT count(*) FROM sources").fetchone()[0],
                      "chunk_count": db.execute("SELECT count(*) FROM chunks").fetchone()[0],
                      "status_counts": counts("status"), "category_counts": counts("category"),
                      "link_count": db.execute("SELECT count(*) FROM links").fetchone()[0],
                      "pending_link_count": db.execute("SELECT count(*) FROM links WHERE review_status='needs_review'").fetchone()[0],
                      "retrieval": "Chinese bigram FTS5/BM25 + query expansion + local reranking",
                      "realtime_connected": False, "training_performed": False}
            result['review_groups'] = group_summary(dict(db.execute(
                "SELECT review_group_id(status,metadata),count(*) FROM sources WHERE status='pending_review' "
                "GROUP BY review_group_id(status,metadata)").fetchall()))
            result['official_origin_confirmed_count'] = db.execute('SELECT count(*) FROM sources s JOIN source_origin_confirmations p ON s.source_id=p.source_id AND s.sha256=p.sha256').fetchone()[0]
            reviews = [json.loads(row[0]) for row in db.execute("SELECT metadata FROM sources WHERE status='approved'")]
            result['technical_reviewed_count'] = sum(meta.get('review_method') == 'official_batch_technical' for meta in reviews)
            result['current_edition_verified_count'] = sum(meta.get('edition_current_verified') is True for meta in reviews)
            # The first-month dashboard needs a safe, aggregate view of the
            # imported directory roots.  Never expose raw paths or source text
            # here; relative_path is reduced to its first component only.
            roots = {}
            for row in db.execute("SELECT relative_path,status,count(*) AS source_count,COALESCE(sum(chunk_count),0) AS chunk_count FROM sources GROUP BY relative_path,status"):
                relative = str(row[0] or "").replace("\\", "/").lstrip("/")
                root = relative.split("/", 1)[0] if relative else "未分类"
                bucket = roots.setdefault(root, {"root": root, "source_count": 0, "chunk_count": 0, "status_counts": {}})
                bucket["source_count"] += int(row[2] or 0)
                bucket["chunk_count"] += int(row[3] or 0)
                state = str(row[1])
                bucket["status_counts"][state] = bucket["status_counts"].get(state, 0) + int(row[2] or 0)
            result["source_roots"] = sorted(roots.values(), key=lambda item: item["root"])
            result["latest_source_updated_at"] = db.execute("SELECT max(updated_at) FROM sources").fetchone()[0]
        result["latest_jobs"] = self.jobs()[:5]
        result["semantic_index"] = "available" if (self.database.parent / "semantic" / "manifest.json").is_file() else "not_built"
        return result

    def new_job(self, payload):
        identity = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO jobs VALUES(?,?,?,0,0,'',?,'[]',?,?)", (identity, "queued", len(payload), dump(payload), now(), now()))
        return identity

    def job(self, identity, private=False):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (identity,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["results"] = json.loads(item["results"])
        if private:
            item["payload"] = json.loads(item["payload"])
        else:
            item.pop("payload", None)
        item["progress"] = round(100 * item["completed"] / max(1, item["total"]), 1)
        return item

    def jobs(self):
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT job_id FROM jobs ORDER BY created_at DESC LIMIT 30")]
        result = []
        for identity in ids:
            item = self.job(identity)
            all_results = item['results']
            issues = [r for r in all_results if r.get('status') in {'error','empty','unsupported','needs_ocr'}]
            item['results'] = (issues + [r for r in all_results if r not in issues])[:10]
            item['results_truncated'] = len(all_results) > 10
            item['result_count'] = len(all_results)
            result.append(item)
        return result

    def update_job(self, identity, **values):
        allowed = {"status", "completed", "failed", "current_file", "results"}
        if not set(values).issubset(allowed):
            raise ValueError("Invalid job update")
        values["updated_at"] = now()
        if "results" in values:
            values["results"] = dump(values["results"])
        with self.connect() as db:
            db.execute("UPDATE jobs SET " + ",".join(k + "=?" for k in values) + " WHERE job_id=?", (*values.values(), identity))
