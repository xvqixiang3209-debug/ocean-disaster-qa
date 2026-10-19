"""Bounded local import queue, immutable files and review-gated indexing."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import zipfile

from .storage import now


ALLOWED = {".pdf", ".doc", ".docx", ".wps", ".pptx", ".xlsx", ".xls", ".txt", ".md", ".csv", ".tsv", ".json", ".geojson", ".zip"}
MAX_FILE = 180 * 1024 * 1024
MAX_EXPANDED = 1536 * 1024 * 1024


def safe_relative(value):
    value = str(value).replace("\\", "/")
    parts = PurePosixPath(value).parts
    if not value or value.startswith("/") or not parts or any(p in {".", ".."} or ":" in p or "\x00" in p for p in parts):
        raise ValueError("非法相对路径")
    if len(value) > 700:
        raise ValueError("文件路径过长")
    return "/".join(parts)


def classify(path):
    text = str(path)
    if any(w in text for w in ("模式计算", "Surge_", "Waterlevel_")):
        return "模式结果"
    if text.lower().endswith((".geojson",)) or "ZJCoastal" in text:
        return "空间数据"
    if any(w in text for w in ("公报",)):
        return "灾害公报"
    if any(w in text for w in ("标准", "规范", "规章", "预案", "导则")):
        return "标准规范"
    if any(w in text for w in ("防办", "任务清单", "落实", "应急会商", "反馈")):
        return "防御响应"
    if any(w in text for w in ("警报", "预报单", "预报202", "解除通报")):
        return "预报警报"
    if any(w in text for w in ("历史", "总结", "灾害")):
        return "历史报告"
    return "其他"


def split_units(units, size=1100, overlap=120):
    """Structure first: never join pages; repeated table headers remain in unit."""
    result = []
    for unit in units:
        text = str(unit.get("text", "")).replace("\x00", "").strip()
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(len(text), start + size)
            if end < len(text):
                positions = [text.rfind(sep, start + size // 2, end) for sep in ("\n", "。", "；")]
                boundary = max(positions)
                if boundary > start:
                    end = boundary + 1
            part = text[start:end].strip()
            if part:
                result.append({"text": part, "locator": str(unit.get("locator", "正文")) + (f" · 字符{start + 1}—{end}" if len(text) > size else ""),
                               "metadata": {**unit.get("metadata", {}), "kind": unit.get("kind", "text"),
                                            "chunk_strategy": "structure_sentence_v1", "char_start": start, "char_end": end}})
            if end >= len(text):
                break
            start = max(start + 1, end - overlap)
    return result


class IngestionService:
    def __init__(self, store, root, history_database=None, parser=None):
        self.store = store
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="marine-import")
        self.active = set()
        self.lock = threading.Lock()
        self.parser = parser
        self.linker = None
        if history_database and Path(history_database).exists():
            from typhoon.entity_linking import TyphoonEntityLinker
            self.linker = TyphoonEntityLinker(history_database)

    def stage(self, stream, relative_path):
        relative = safe_relative(relative_path)
        ext = Path(relative).suffix.lower()
        if ext not in ALLOWED:
            raise ValueError("暂不支持文件类型：" + ext)
        import tempfile
        fd, name = tempfile.mkstemp(prefix="upload-", suffix=ext, dir=self.root)
        digest = hashlib.sha256(); size = 0
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > MAX_FILE:
                        raise ValueError("单文件超过180 MiB，请在本地分批提供")
                    out.write(block); digest.update(block)
            raw = self.root / "raw" / (digest.hexdigest() + ext)
            raw.parent.mkdir(exist_ok=True)
            if raw.exists():
                Path(name).unlink()  # Only the explicit temporary file created above.
            else:
                Path(name).rename(raw)
            return {"path": str(raw), "relative_path": relative, "sha256": digest.hexdigest(), "bytes": size}
        except Exception:
            candidate = Path(name)
            if candidate.is_file() and candidate.parent == self.root:
                candidate.unlink()
            raise

    def submit(self, payload):
        job = self.store.new_job(payload)
        self.resume(job)
        return job

    def resume(self, job_id):
        with self.lock:
            if job_id in self.active:
                return
            self.active.add(job_id)
        self.pool.submit(self._run, job_id)

    def recover(self):
        with self.store.connect() as db:
            ids = [row[0] for row in db.execute("SELECT job_id FROM jobs WHERE status IN ('queued','running','interrupted') ORDER BY created_at")]
        for identity in ids:
            self.resume(identity)

    def _archive(self, entry):
        children = []
        with zipfile.ZipFile(entry["path"]) as archive:
            infos = [i for i in archive.infolist() if not i.is_dir()]
            if len(infos) > 4000 or sum(i.file_size for i in infos) > MAX_EXPANDED:
                raise ValueError("ZIP展开超过4000文件或1.5 GiB")
            # Validate the complete manifest before staging any member.
            for info in infos:
                relative = safe_relative(info.filename)
                if info.flag_bits & 1:
                    raise ValueError("加密ZIP请先本地解压")
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError("ZIP符号链接不允许")
                if info.file_size > MAX_FILE or info.file_size > max(1, info.compress_size) * 200:
                    raise ValueError("ZIP大小或压缩比超限")
                if Path(relative).suffix.lower() == ".zip":
                    raise ValueError("不支持嵌套ZIP，请先解压")
            for info in infos:
                relative = safe_relative(info.filename)
                if Path(relative).suffix.lower() not in ALLOWED:
                    children.append({"relative_path": relative, "error": "不支持的文件类型"})
                    continue
                with archive.open(info) as stream:
                    child = self.stage(stream, Path(entry["relative_path"]).stem + "/" + relative)
                children.append(child)
        return children

    def import_entry(self, entry, *, force=False):
        if entry.get("error"):
            return {"filename": entry["relative_path"], "status": "error", "error": entry["error"]}
        relative = safe_relative(entry["relative_path"])
        path = Path(entry["path"]).resolve(strict=True)
        # All input must have been staged in this service's immutable store.
        if not path.is_relative_to(self.root / "raw") or path.is_symlink():
            raise ValueError("导入路径不在暂存范围")
        source_id, created = self.store.register(filename=Path(relative).name, relative_path=relative,
            sha256=entry["sha256"], size=entry["bytes"], raw_path=path, category=classify(relative))
        old = self.store.get_source(source_id)
        if not force and not created and old["status"] not in {"queued", "error", "unsupported"}:
            return {"source_id": source_id, "filename": relative, "status": old["status"], "reused": True}
        if Path(relative).name.startswith("~$"):
            parsed = {"status": "empty", "units": [], "metadata": {}, "warnings": ["Office临时锁文件不入库"]}
            target_status = "ignored"
        elif not entry["bytes"]:
            parsed = {"status": "empty", "units": [], "metadata": {}, "warnings": ["文件为空，请补交"]}
            target_status = "empty"
        else:
            if self.parser is None:
                from .parsers import parse_file
                parser = parse_file
            else:
                parser = self.parser
            parsed = parser(path)
            target_status = None
        chunks = split_units(parsed.get("units", []))
        metadata = {**parsed.get("metadata", {}), "external_allowed": False,
                    "source_type": "historical_archive", "original_path": relative,
                    "parser_version": "phase1.1", "chunk_strategy": "structure_sentence_v1"}
        category = classify(relative)
        text = "\n".join(c["text"] for c in chunks[:8])
        from .governance import draft_markers
        draft = draft_markers(relative, text)
        is_draft = draft['is_draft']
        metadata['references_draft'] = draft['references_draft']
        metadata["is_draft"] = is_draft
        metadata["edition_status"] = "draft" if is_draft else "unverified"
        years = sorted(set(re.findall(r"(?<!\d)((?:19|20)\d{2})年", text)))
        if len(years) == 1:
            metadata["event_year"] = int(years[0])
        quality = parsed.get("status", "error")
        warnings = list(parsed.get("warnings", []))
        if parsed.get("metadata", {}).get("quarantined_pages"):
            metadata["requires_human_review"] = True
            if quality == "parsed":
                quality = "partial"
        if not target_status and (metadata.get("ingest_mode") == "structured_only" or category in {"模式结果", "空间数据"}):
            target_status = "pending_review"
            chunks = []
            warnings.append("结构资料仅暂存元数据；单位、时间、基准审核前不进入正文RAG或超警计算")
        if not target_status:
            if quality in {"unsupported", "empty", "error"}:
                target_status = quality
            elif quality == "needs_ocr" or not chunks:
                target_status = "needs_ocr"
            elif is_draft or category == "标准规范" or parsed.get("metadata", {}).get("requires_human_review") or metadata.get("ocr", {}).get("requires_human_review"):
                target_status = "pending_review"
            else:
                target_status = "indexed"
        if is_draft:
            warnings.append("草案/报批/上报版本不能自动认定现行正式规则")
        if target_status == "indexed":
            warnings.append("历史参考资料，未经过专家审定，不能作为当前业务状态")
        self.store.finish(source_id, status=target_status, quality_status=quality,
                          metadata=metadata, warnings=warnings, chunks=chunks, linker=self.linker)
        return {"source_id": source_id, "filename": relative, "status": target_status,
                "chunk_count": len(chunks), "warnings": warnings[:6]}

    def _run(self, identity):
        # OS-held file lock is released on process exit; web/CLI cannot process
        # the same persistent job concurrently after a service restart.
        lock_dir = self.root / "job_locks"
        lock_dir.mkdir(exist_ok=True)
        lock_file = (lock_dir / (identity + ".lock")).open("a+b")
        try:
            lock_file.seek(0)
            if lock_file.read(1) == b"":
                lock_file.write(b"0"); lock_file.flush()
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock_file.close()
            with self.lock:
                self.active.discard(identity)
            return
        job = self.store.job(identity, private=True)
        results = list(job["results"])
        completed, failed = job["completed"], job["failed"]
        try:
            self.store.update_job(identity, status="running")
            for index, entry in enumerate(job["payload"]):
                if index < completed:
                    continue
                self.store.update_job(identity, current_file=entry["relative_path"])
                try:
                    if not entry.get('error') and Path(entry["path"]).suffix.lower() == ".zip":
                        children = self._archive(entry)
                        child_id = self.submit(children)
                        result = {"filename": entry["relative_path"], "status": "expanded", "child_job_id": child_id, "file_count": len(children)}
                    else:
                        result = self.import_entry(entry)
                    if result["status"] in {"error", "unsupported", "empty", "needs_ocr"}:
                        failed += 1
                except Exception as exc:
                    failed += 1
                    result = {"filename": entry["relative_path"], "status": "error", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
                results.append(result)
                completed = index + 1
                self.store.update_job(identity, results=results, completed=completed, failed=failed)
            self.store.update_job(identity, status="completed_with_issues" if failed else "completed", current_file="")
        except Exception:
            self.store.update_job(identity, status="interrupted")
            raise
        finally:
            lock_file.close()
            with self.lock:
                self.active.discard(identity)
