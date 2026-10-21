"""Local-only governance API. Shared-network deployment requires admin auth."""
import hmac
import ipaddress
import json
import secrets
from pathlib import Path
from urllib.parse import urlsplit
from flask import Blueprint, jsonify, request, send_file
from .requirements import load_catalog, status_summary
from .acceptance import MonthOneAcceptance


def create_blueprint(store, ingestion, qa, requirements_path=None, acceptance_directory=None):
    bp = Blueprint("marine_knowledge", __name__, url_prefix="/api/knowledge")
    token = secrets.token_urlsafe(32)
    catalog_path = Path(requirements_path) if requirements_path is not None else Path(__file__).resolve().parents[1] / "data" / "requirements" / "catalog.json"
    acceptance = MonthOneAcceptance(store, acceptance_directory or Path(__file__).resolve().parents[1] / 'docs')
    from .library_catalog import LibraryCatalog
    libraries = LibraryCatalog(store, getattr(qa, 'legacy_rag', None))

    @bp.before_request
    def local_admin_boundary():
        # This phase is a local workbench, not a publicly exposed admin system.
        try:
            if not ipaddress.ip_address(request.remote_addr or "").is_loopback:
                return jsonify(error="知识工作台仅允许本机访问，局域网部署前请配置独立管理员鉴权"), 403
        except ValueError:
            return jsonify(error="无法核实本机请求"), 403
        host = urlsplit(request.host_url).hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return jsonify(error="请使用127.0.0.1或localhost访问工作台"), 403
        if request.method != "GET" and not hmac.compare_digest(request.headers.get("X-Knowledge-Token", ""), token):
            return jsonify(error="工作台会话已更新，请刷新页面"), 403
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            return jsonify(error="拒绝跨站请求"), 403

    @bp.after_request
    def private(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @bp.get("/status")
    def status():
        catalog = load_catalog(catalog_path)
        states = store.requirement_states()
        items = [{**item, **states.get(item["id"], {})} for item in catalog.get("items", [])]
        return jsonify(**store.status(), requirements={"available": catalog["available"],
            "source_sha256": catalog.get("source_sha256"), **status_summary(items)},
            external_model=qa.model_status() if qa is not None else {
                "state": "unavailable", "enabled": False, "configured": False,
                "tested": False, "note": "问答服务未安装。"}, csrf_token=token)

    @bp.get('/numeric/status')
    def numeric_status():
        from .numeric_access import open_archive
        try:
            return jsonify(available=True, **open_archive(store).summary())
        except FileNotFoundError:
            return jsonify(available=False, note='数值归档尚未建立'), 404

    @bp.get('/numeric/query')
    def numeric_query():
        from .numeric_access import open_archive
        try:
            if not any(request.args.get(key) for key in ('file', 'station')):
                raise ValueError('请至少指定文件或地点列名')
            return jsonify(**open_archive(store).query(file=request.args.get('file'), station=request.args.get('station'),
                time_token=request.args.get('time_token'), start=request.args.get('start'), end=request.args.get('end'),
                limit=min(100, max(1, int(request.args.get('limit', 40))))))
        except FileNotFoundError:
            return jsonify(error='数值归档尚未建立'), 404
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @bp.get('/numeric/coasts')
    def numeric_coasts():
        from .numeric_access import open_archive
        try:
            archive = open_archive(store)
            return jsonify(items=archive.coasts(query=request.args.get('q', ''), version=request.args.get('version')),
                           conflicts=archive.coast_conflicts(), note='并列保留原GeoJSON和SQL版本，不自动选用冲突阈值。')
        except FileNotFoundError:
            return jsonify(error='数值归档尚未建立'), 404

    @bp.post("/model-test")
    def model_test():
        if qa is None:
            return jsonify(error="问答服务未安装"), 503
        current = qa.model_status()
        if current["state"] in {"disabled", "not_configured"}:
            return jsonify(error=current["note"], **current), 503
        try:
            return jsonify(qa.test_model_connection())
        except Exception as exc:
            return jsonify(error=f"模型连接失败：{exc.__class__.__name__}", **qa.model_status()), 502

    @bp.get("/requirements")
    def requirements():
        catalog = load_catalog(catalog_path)
        if not catalog["available"]:
            return jsonify(available=False, error="需求台账尚未构建", items=[], total=0, categories=[]), 503
        try:
            page = max(1, int(request.args.get("page", 1)))
            limit = min(100, max(1, int(request.args.get("limit", 20))))
        except ValueError:
            return jsonify(error="页码或每页条数无效"), 400
        query = request.args.get("q", "")[:200].strip().lower()
        category = request.args.get("category", "").strip()
        month = request.args.get("month", "").strip()
        role = request.args.get("role", "").strip()
        scope = request.args.get("scope", "").strip()
        status_filter = request.args.get("status", "").strip()
        states = store.requirement_states()
        all_items = [{**item, **states.get(item["id"], {})} for item in catalog["items"]]
        items = [item for item in all_items
                 if (not query or query in (item["id"] + " " + item["question"] + " " + item["subrequirement"]).lower())
                 and (not category or item["category_id"] == category)
                 and (not month or item["first_acceptance_window"] == month)
                 and (not role or (item["role"] or "通用") == role)
                 and (not scope or item["planning_scope"] == scope)
                 and (not status_filter or item["status"] == status_filter)]
        start = (page - 1) * limit
        return jsonify(available=True, source_name=catalog["source_name"],
            source_sha256=catalog["source_sha256"], categories=catalog["categories"],
            roles=sorted({item["role"] or "通用" for item in all_items}),
            summary=status_summary(all_items), items=items[start:start + limit],
            total=len(items), page=page, page_size=limit)

    @bp.post("/requirements/<question_id>/state")
    def requirement_state(question_id):
        body = request.get_json(silent=True) or {}
        try:
            catalog = load_catalog(catalog_path)
            if question_id not in {item["id"] for item in catalog.get("items", [])}:
                return jsonify(error="题目编号不在当前需求台账中"), 404
            result = store.update_requirement_state(question_id, body.get("status"), body.get("note", ""), body.get("actor", ""))
            return jsonify(question_id=question_id, **result)
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400

    @bp.get("/requirements/<question_id>/history")
    def requirement_history(question_id):
        catalog = load_catalog(catalog_path)
        if question_id not in {item["id"] for item in catalog.get("items", [])}:
            return jsonify(error="题目编号不在当前需求台账中"), 404
        with store.connect() as db:
            rows = db.execute("SELECT previous_status,status,note,actor,created_at FROM requirement_state_log WHERE question_id=? ORDER BY id DESC LIMIT 100", (question_id,)).fetchall()
        return jsonify(items=[dict(row) for row in rows])

    @bp.get("/inventory")
    def inventory():
        current = store.status()
        return jsonify(source_roots=current["source_roots"], status_counts=current["status_counts"],
            source_count=current["source_count"], chunk_count=current["chunk_count"],
            latest_source_updated_at=current["latest_source_updated_at"],
            note="本机资料快照；已接收不等于已批准或已同步到其他服务器。")

    @bp.get('/month1-acceptance')
    def month1_acceptance():
        return jsonify(acceptance.snapshot())

    @bp.post('/month1-acceptance/<case_id>/review')
    def month1_acceptance_review(case_id):
        try:
            return jsonify(acceptance.review(case_id, request.get_json(silent=True)))
        except KeyError:
            return jsonify(error='候选题不存在'), 404
        except (ValueError, TypeError, AttributeError) as exc:
            return jsonify(error=str(exc)), 400

    @bp.get("/sources")
    def sources():
        try:
            if 'library' in request.args:
                return jsonify(libraries.list_sources(library=request.args.get('library','all'),
                    q=request.args.get('q',''),status=request.args.get('status',''),category=request.args.get('category',''),
                    page=request.args.get('page',1),limit=request.args.get('limit',40),
                    review_group=request.args.get('review_group','')))
            return jsonify(store.list_sources(q=request.args.get("q", "")[:300],
                category=request.args.get("category", ""), status=request.args.get("status", ""),
                page=int(request.args.get("page", 1)),limit=int(request.args.get('limit',40)),
                review_group=request.args.get('review_group','')))
        except ValueError as exc:
            return jsonify(error=str(exc) if 'library' in request.args or 'review_group' in request.args else "页码无效"), 400

    @bp.get('/libraries')
    def library_summary():return jsonify(libraries.summary())

    @bp.get("/sources/<source_id>")
    def detail(source_id):
        result = qa.legacy_source_detail(source_id) if source_id.startswith("legacy:") and qa is not None else store.detail(source_id)
        if result and qa is not None and hasattr(qa, '_can_send'):
            result['source']['external_allowed'] = any(qa._can_send({**chunk, 'source_id':source_id,
                'metadata':result['source'].get('metadata', {})}) for chunk in result.get('chunks', []))
        return (jsonify(result), 200) if result else (jsonify(error="资料不存在"), 404)

    @bp.get("/sources/<source_id>/file")
    def original_file(source_id):
        if source_id.startswith("legacy:"):
            return jsonify(error="旧历史库未保存原件；请向资料提供方核验原始文件，本页仅能查看保留的文本切片。"), 404
        with store.connect() as db:
            row = db.execute("SELECT raw_path,filename FROM sources WHERE source_id=?", (source_id,)).fetchone()
        if not row:
            return jsonify(error="资料不存在"), 404
        raw = Path(row['raw_path'])
        try:
            resolved = raw.resolve(strict=True)
        except (FileNotFoundError, OSError):
            return jsonify(error="原始文件缺失，请检查资料存储"), 404
        if raw.is_symlink() or not resolved.is_relative_to(ingestion.root / 'raw') or not resolved.is_file():
            return jsonify(error="资料存储路径无效"), 403
        response = send_file(resolved, as_attachment=True, download_name=row['filename'], mimetype='application/octet-stream')
        response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
        return response

    @bp.post("/sources/<source_id>/review")
    def review(source_id):
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(source=store.review(source_id, body.get("status"), str(body.get("note", "")), body.get("external_allowed")))
        except KeyError:
            return jsonify(error="资料不存在"), 404
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400

    @bp.post("/import")
    def import_files():
        if (request.content_length or 0) > 250 * 1024 * 1024:
            return jsonify(error="每批最多250 MiB，请分批上传"), 413
        uploads = [u for u in request.files.getlist("files") if u.filename]
        if not uploads or len(uploads) > 200:
            return jsonify(error="每批请选择1至200个文件"), 400
        try:
            paths = json.loads(request.form.get("relative_paths", "[]"))
            if not isinstance(paths, list) or (paths and len(paths) != len(uploads)):
                raise ValueError("文件相对路径数量不匹配")
            staged = []
            for index, upload in enumerate(uploads):
                staged.append(ingestion.stage(upload.stream, paths[index] if paths else upload.filename))
            return jsonify(job_id=ingestion.submit(staged)), 202
        except (ValueError, json.JSONDecodeError) as exc:
            return jsonify(error=str(exc)), 400

    @bp.post('/links/review')
    def link_review():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(store.review_link(int(body.get('chunk_id')), str(body.get('sid','')), body.get('status'), str(body.get('note',''))))
        except KeyError:
            return jsonify(error='关联候选不存在'),404
        except (TypeError,ValueError) as exc:
            return jsonify(error=str(exc)),400

    @bp.get("/jobs")
    def jobs():
        return jsonify(items=store.jobs())

    @bp.get("/jobs/<job_id>")
    def job(job_id):
        item = store.job(job_id)
        return (jsonify(item), 200) if item else (jsonify(error="任务不存在"), 404)

    @bp.post("/jobs/<job_id>/resume")
    def resume(job_id):
        item = store.job(job_id)
        if not item:
            return jsonify(error="任务不存在"), 404
        ingestion.resume(job_id)
        return jsonify(job_id=job_id)

    @bp.post("/ask")
    def ask():
        body = request.get_json(silent=True) or {}
        question = str(body.get("question", "")).strip()
        if not question or len(question) > 2000:
            return jsonify(error="问题应为1至2000字"), 400
        return jsonify(qa.answer(question, model_name=body.get("model"),
            sid=body.get("sid"), allow_web=body.get("allow_web", True) is True))

    return bp
