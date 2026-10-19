"""Flask API adapter for the typhoon application service."""

from __future__ import annotations

import json

from flask import Blueprint, jsonify, request, current_app

from .bootstrap import TyphoonComponents
from .config import SIMILARITY_WEIGHTS
from .repositories import RepositoryConfigurationError
from .schema import TyphoonInputError
from .storm_surge_import import (
    merge_storm_surge_events,
    prepare_storm_surge_workbook,
    storm_surge_event_summary,
    storm_surge_rag_summary,
)
from .validation import FactValidationError
from .rag import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    MAX_BATCH_FILES,
    MAX_BATCH_UPLOAD_BYTES,
    MAX_MULTIPART_OVERHEAD_BYTES,
    MAX_UPLOAD_BYTES,
    KnowledgeImportError,
    normalize_relative_path,
    normalize_pdf_split_strategy,
    upload_size_limit,
)


CONTROL_FIELDS = {"top_k", "report_mode", "model", "request_id", "compact"}


def create_typhoon_blueprint(components: TyphoonComponents) -> Blueprint:
    blueprint = Blueprint("typhoon_api", __name__, url_prefix="/api/typhoon")

    @blueprint.get("/status")
    def status():
        data = components.application.public_status()
        data["current_provider"] = {
            "available": components.current_provider.available,
            "type": "zjocean_live" if hasattr(components.current_provider, 'sync') else "file",
        }
        data["similarity"] = {
            "primary_features": ["track"],
            "weights": SIMILARITY_WEIGHTS,
            "top_k_default": 10,
        }
        data["rag"] = components.rag.status()
        data["web_search"] = components.web_search.status()
        data["web_reading"] = {
            **components.conversations.web_reader.status(),
            "summary_timeout_seconds": components.settings.web_summary_timeout_seconds,
        }
        return jsonify(data)

    @blueprint.get("/current")
    def current():
        try:
            return jsonify(components.current_provider.get_current_typhoon())
        except (FileNotFoundError, TyphoonInputError, json.JSONDecodeError) as exc:
            return jsonify({"error": str(exc), "code": "CURRENT_TYPHOON_NOT_READY"}), 404

    @blueprint.post("/similar")
    def similar():
        try:
            body = request.get_json(silent=True)
            if body is not None and not isinstance(body, dict):
                raise TyphoonInputError("请求体必须是 JSON 对象")
            body = body or {}
            try:
                top_k = int(body.get("top_k", 10))
            except (TypeError, ValueError) as exc:
                raise TyphoonInputError("top_k 必须是整数") from exc
            if not body or set(body.keys()) <= CONTROL_FIELDS:
                payload = components.current_provider.get_current_typhoon()
            else:
                payload = body
            result = components.application.analyze(
                payload,
                top_k=top_k,
                request_id=body.get("request_id"),
                report_mode=body.get("report_mode"),
                model_name=body.get("model"),
                compact=bool(body.get("compact", False)),
            )
            return jsonify(result)
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc), "code": "DATA_NOT_READY"}), 503
        except TyphoonInputError as exc:
            return jsonify({"error": str(exc), "code": "INVALID_CURRENT_TYPHOON"}), 400
        except RepositoryConfigurationError as exc:
            return jsonify({"error": str(exc), "code": "DATA_NOT_READY"}), 503
        except FactValidationError as exc:
            return jsonify({"error": str(exc), "code": "OUTPUT_VALIDATION_FAILED"}), 500
        except ValueError as exc:
            return jsonify({"error": str(exc), "code": "INVALID_REQUEST"}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc), "code": "MATCH_FAILED"}), 422
        except Exception as exc:
            print(f"[Typhoon Error] {exc}")
            return jsonify({"error": f"台风类比失败: {exc}", "code": "INTERNAL_ERROR"}), 500

    @blueprint.post("/conversation/select")
    def select_analogue():
        body = request.get_json(silent=True) or {}
        try:
            request_id = str(body.get("request_id") or "").strip()
            sid = str(body.get("sid") or "").strip()
            if not request_id or not sid:
                raise ValueError("request_id 和 sid 不能为空")
            return jsonify(components.conversations.select(request_id, sid))
        except KeyError as exc:
            return jsonify({"error": str(exc).strip("'"), "code": "SESSION_NOT_FOUND"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc), "code": "INVALID_REQUEST"}), 400

    @blueprint.post("/conversation/ask")
    def ask_selected_analogue():
        body = request.get_json(silent=True) or {}
        try:
            request_id = str(body.get("request_id") or "").strip()
            sid = str(body.get("sid") or "").strip()
            question = str(body.get("question") or "").strip()
            if not request_id or not sid:
                raise ValueError("请先从检索结果中选择一个台风")
            if not question or len(question) > 2000:
                raise ValueError("问题应为1至2000字")
            # New governed evidence is considered locally first. Never pass
            # newly imported unapproved-for-external documents to the legacy
            # model prompt. The numerical/path and structured fallback stay intact.
            extension = current_app.extensions.get("marine_kb", {})
            # Structured station tables and path calculations keep their
            # existing numerical service. Other selected-case questions share
            # the same always-local-and-public-web policy as the main chat.
            structured_question = any(word in question for word in (
                '超警', '分站', '各站', '经纬度', '相似度', '排名', '轨迹'))
            unified_qa = extension.get('qa') if not structured_question else None
            if unified_qa is not None and not unified_qa.require_model_answer:
                unified_qa = None
            selected_qa = unified_qa or extension.get("selected_local_qa")
            if selected_qa is not None:
                analogue = components.conversations.sessions.select(request_id, sid)
                identity = f"{analogue.get('season','')}年{analogue.get('name','')}台风"
                options = {'sid':sid, 'allow_web':unified_qa is not None}
                if hasattr(selected_qa, 'prepare_question'):
                    session = components.conversations.sessions.get(request_id) or {}
                    # Legacy sessions did not store source permissions with their
                    # answers. Reuse only user questions; the integrated map API
                    # keeps governed answer context in its own conversation store.
                    options['history'] = [row for row in session.get('history', {}).get(sid, [])
                                          if row.get('role') in {'用户','user'}]
                    options['selected_context'] = {**{key:analogue.get(key) for key in ('name','season')},'sid':sid}
                    grounded = selected_qa.answer(question, **options)
                else:
                    grounded = selected_qa.answer(identity + " " + question, **options)
                if (unified_qa is not None or grounded.get("rag_sources") or grounded.get('answer_meta', {}).get('dynamic_question')
                        or any(word in question for word in ('标准','规范','预案','规章','条款','阈值','法定','分级条件','判定条件','发布条件'))):
                    components.conversations.sessions.append_history(request_id, sid, "用户", question)
                    components.conversations.sessions.append_history(request_id, sid, "助手", grounded["answer"])
                    grounded.update(request_id=request_id, selected_sid=sid,
                        evidence=[{"filename": s.get("filename"), "chunk_index": s.get("locator"), "content":s.get("excerpt"), "source_id":s.get("source_id")} for s in grounded.get("rag_sources", [])])
                    return jsonify(grounded)
            return jsonify(
                components.conversations.ask(
                    request_id,
                    sid,
                    question,
                    model_name=body.get("model"),
                )
            )
        except KeyError as exc:
            return jsonify({"error": str(exc).strip("'"), "code": "SESSION_NOT_FOUND"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc), "code": "INVALID_REQUEST"}), 400

    @blueprint.get("/knowledge/status")
    def knowledge_status():
        return jsonify(components.rag.status())

    @blueprint.get("/knowledge/documents")
    def knowledge_documents():
        return jsonify({"documents": components.rag.list_documents()})

    @blueprint.post("/knowledge/import")
    def knowledge_import():
        try:
            # Check the declared request size before Flask materializes a large
            # multipart body.  The second check below uses the actual bytes.
            if request.content_length and request.content_length > (
                MAX_BATCH_UPLOAD_BYTES + MAX_MULTIPART_OVERHEAD_BYTES
            ):
                raise KnowledgeImportError("单次批量上传总量不得超过 200 MB")
            uploads = request.files.getlist("files")
            if not uploads:
                # Backward compatibility with the original single-file API.
                uploads = request.files.getlist("file")
            uploads = [upload for upload in uploads if upload is not None and upload.filename]
            if not uploads:
                raise KnowledgeImportError("请选择要导入的知识文件")
            if len(uploads) > MAX_BATCH_FILES:
                raise KnowledgeImportError(f"单次最多选择 {MAX_BATCH_FILES} 个文件")
            pdf_split_strategy = normalize_pdf_split_strategy(
                request.form.get("pdf_split_strategy", "pages")
            )
            raw_chunk_size = request.form.get("chunk_size")
            raw_overlap = request.form.get("overlap")
            if pdf_split_strategy == "semantic":
                # In semantic mode the model chooses one reviewed profile for
                # each original PDF. Empty/auto are therefore valid. Numeric
                # legacy fields are accepted but deliberately ignored for PDFs.
                chunk_size = DEFAULT_CHUNK_SIZE
                overlap = DEFAULT_CHUNK_OVERLAP
            else:
                try:
                    chunk_size = int(raw_chunk_size or DEFAULT_CHUNK_SIZE)
                    overlap = int(raw_overlap or DEFAULT_CHUNK_OVERLAP)
                except (TypeError, ValueError) as exc:
                    raise KnowledgeImportError("切片长度和重叠长度必须是整数") from exc

            relative_paths = request.form.getlist("relative_paths")
            imported: list[dict] = []
            file_results: list[dict] = []
            structured_storm_surge_events: list[dict] = []
            total_bytes = 0
            for index, upload in enumerate(uploads):
                requested_path = relative_paths[index] if index < len(relative_paths) else upload.filename
                try:
                    relative_path = normalize_relative_path(requested_path, upload.filename)
                    source_limit = upload_size_limit(upload.filename)
                    data = upload.read(source_limit + 1)
                    if len(data) > source_limit:
                        if str(upload.filename).lower().endswith(".pdf"):
                            raise KnowledgeImportError(
                                f"单个 PDF 不得超过 {source_limit // (1024 * 1024)} MB"
                            )
                        raise KnowledgeImportError(
                            f"单个非 PDF 文件不得超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
                        )
                    total_bytes += len(data)
                    if total_bytes > MAX_BATCH_UPLOAD_BYTES:
                        raise KnowledgeImportError("单次批量上传总量不得超过 200 MB")
                    structured_event = None
                    structured_summary = None
                    history_database_path = components.rag.history_database_path
                    if history_database_path is not None:
                        try:
                            structured_event = prepare_storm_surge_workbook(
                                upload.filename,
                                data,
                                history_database_path,
                                relative_path=relative_path,
                            )
                        except Exception as exc:
                            raise KnowledgeImportError(
                                f"风暴潮工作簿解析或关联失败: {exc}"
                            ) from exc
                    import_metadata = {"relative_path": relative_path}
                    if structured_event is not None:
                        structured_summary = storm_surge_event_summary(structured_event)
                        import_metadata.update(
                            {
                                "structured_data_type": "storm_surge_observations",
                                "storm_code": structured_event["storm_code"],
                                "season": structured_event["season"],
                                "storm_name": structured_event.get("storm_name"),
                                "storm_name_cn": structured_event.get("storm_name_cn"),
                                "sid": structured_event.get("sid"),
                                "storm_link_method": structured_event["link_method"],
                                "storm_link_confidence": structured_event["link_confidence"],
                                "station_count": structured_event["station_count"],
                                "observation_count": structured_event["observation_count"],
                                "structured_rag_summary": storm_surge_rag_summary(structured_event),
                            }
                        )
                    documents = components.rag.import_bytes(
                        upload.filename,
                        data,
                        chunk_size=chunk_size,
                        overlap=overlap,
                        source_kind="folder_upload" if "/" in relative_path else "user_upload",
                        metadata=import_metadata,
                        pdf_split_strategy=pdf_split_strategy,
                        auto_link=False,
                    )
                    imported.extend(documents)
                    if structured_event is not None:
                        structured_storm_surge_events.append(structured_event)
                    auto_split = any(
                        bool((document.get("metadata") or {}).get("auto_split"))
                        for document in documents
                    )
                    split_metadata = (documents[0].get("metadata") or {}) if auto_split else {}
                    used_strategies = sorted(
                        {
                            str((document.get("metadata") or {}).get("split_strategy_used"))
                            for document in documents
                            if (document.get("metadata") or {}).get("split_strategy_used")
                        }
                    )
                    file_results.append(
                        {
                            "index": index,
                            "filename": upload.filename,
                            "relative_path": relative_path,
                            "status": "success",
                            "documents": documents,
                            "document_count": len(documents),
                            "auto_split": auto_split,
                            "split_part_count": len(documents) if auto_split else 0,
                            "source_byte_size": len(data),
                            "requested_split_strategy": (
                                split_metadata.get("requested_split_strategy")
                                or (pdf_split_strategy if str(upload.filename).lower().endswith(".pdf") else None)
                            ),
                            "split_strategy_used": (
                                used_strategies[0]
                                if len(used_strategies) == 1
                                else ("semantic_hybrid" if used_strategies else (
                                    "not_split" if str(upload.filename).lower().endswith(".pdf") else None
                                ))
                            ),
                            "split_strategies_used": used_strategies,
                            "split_strategy_reason": split_metadata.get("split_strategy_reason") or "",
                            "split_fallback_reason": split_metadata.get("split_fallback_reason") or "",
                            "boundary_sources": sorted(
                                {
                                    str((document.get("metadata") or {}).get("boundary_source"))
                                    for document in documents
                                    if (document.get("metadata") or {}).get("boundary_source")
                                }
                            ),
                            "requested_chunk_size": (
                                "auto"
                                if str(upload.filename).lower().endswith(".pdf")
                                and pdf_split_strategy == "semantic"
                                else (documents[0].get("requested_chunk_size") if documents else chunk_size)
                            ),
                            "requested_overlap": (
                                "auto"
                                if str(upload.filename).lower().endswith(".pdf")
                                and pdf_split_strategy == "semantic"
                                else (documents[0].get("requested_overlap") if documents else overlap)
                            ),
                            "actual_chunk_size": (
                                next(iter({
                                    document.get("actual_chunk_size")
                                    for document in documents
                                    if document.get("actual_chunk_size") is not None
                                }), None)
                                if len({
                                    document.get("actual_chunk_size")
                                    for document in documents
                                    if document.get("actual_chunk_size") is not None
                                }) <= 1
                                else None
                            ),
                            "actual_overlap": (
                                next(iter({
                                    document.get("actual_overlap")
                                    for document in documents
                                    if document.get("actual_overlap") is not None
                                }), None)
                                if len({
                                    document.get("actual_overlap")
                                    for document in documents
                                    if document.get("actual_overlap") is not None
                                }) <= 1
                                else None
                            ),
                            "chunking_source": (
                                next(iter({
                                    str(document.get("chunking_source"))
                                    for document in documents
                                    if document.get("chunking_source")
                                }), "")
                                if len({
                                    str(document.get("chunking_source"))
                                    for document in documents
                                    if document.get("chunking_source")
                                }) <= 1
                                else "mixed"
                            ),
                            "chunking_model": next(
                                (
                                    str(document.get("chunking_model") or "")
                                    for document in documents
                                    if document.get("chunking_model")
                                ),
                                "",
                            ),
                            "chunking_reason": next(
                                (
                                    str(document.get("chunking_reason") or "")
                                    for document in documents
                                    if document.get("chunking_reason")
                                ),
                                "",
                            ),
                            "chunking_fallback_reason": next(
                                (
                                    str(document.get("chunking_fallback_reason") or "")
                                    for document in documents
                                    if document.get("chunking_fallback_reason")
                                ),
                                "",
                            ),
                            "chunk_config_applied": all(
                                bool(document.get("chunk_config_applied")) for document in documents
                            ),
                            "duplicate_chunking_preserved": any(
                                bool(document.get("duplicate_chunking_preserved")) for document in documents
                            ),
                        }
                    )
                    file_results[-1]["structured_storm_surge"] = structured_summary
                    legacy_documents = [
                        document for document in documents
                        if (document.get("metadata") or {}).get("structured_data_type")
                        == "legacy_storm_surge_year_doc"
                    ]
                    file_results[-1].update(
                        {
                            "legacy_doc_event_count": len(legacy_documents),
                            "legacy_doc_high_confidence_count": sum(
                                (item.get("metadata") or {}).get("candidate_link_confidence") == "high"
                                for item in legacy_documents
                            ),
                            "legacy_doc_needs_review_count": sum(
                                (item.get("metadata") or {}).get("candidate_link_confidence")
                                in {"medium", "low"}
                                for item in legacy_documents
                            ),
                            "legacy_doc_unlinked_count": sum(
                                (item.get("metadata") or {}).get("candidate_link_confidence") == "unlinked"
                                for item in legacy_documents
                            ),
                        }
                    )
                except KnowledgeImportError as exc:
                    file_results.append(
                        {
                            "index": index,
                            "filename": upload.filename,
                            "relative_path": str(requested_path or upload.filename),
                            "status": "failed",
                            "error": str(exc),
                            "documents": [],
                            "document_count": 0,
                            "auto_split": False,
                            "split_part_count": 0,
                            "requested_split_strategy": (
                                pdf_split_strategy if str(upload.filename).lower().endswith(".pdf") else None
                            ),
                            "split_strategy_used": None,
                            "split_strategies_used": [],
                            "split_strategy_reason": "",
                            "split_fallback_reason": "",
                            "boundary_sources": [],
                            "requested_chunk_size": (
                                "auto"
                                if str(upload.filename).lower().endswith(".pdf")
                                and pdf_split_strategy == "semantic"
                                else chunk_size
                            ),
                            "requested_overlap": (
                                "auto"
                                if str(upload.filename).lower().endswith(".pdf")
                                and pdf_split_strategy == "semantic"
                                else overlap
                            ),
                            "actual_chunk_size": None,
                            "actual_overlap": None,
                            "chunking_source": "",
                            "chunking_model": "",
                            "chunking_reason": "",
                            "chunking_fallback_reason": "",
                            "chunk_config_applied": False,
                            "duplicate_chunking_preserved": False,
                        }
                    )

            structured_import = merge_storm_surge_events(
                components.rag.history_database_path,
                structured_storm_surge_events,
                source_label="visual_knowledge_folder_upload",
            ) if structured_storm_surge_events else {
                "event_count": 0,
                "linked_event_count": 0,
                "station_count": 0,
                "observation_count": 0,
                "high_confidence_links": 0,
                "time_overlap_links": 0,
            }

            # A folder can contain hundreds of files.  Link once after the
            # batch instead of rebuilding the whole relation table per file.
            linking_run = components.rag.refresh_storm_links()
            for document in imported:
                document["storm_linking"] = components.rag.document_link_status(
                    document["document_id"]
                )
                document["storm_linking"]["run"] = {
                    key: linking_run.get(key)
                    for key in ("available", "method_version", "error")
                    if key in linking_run
                }
            unique_imported = {
                item["document_id"]: item for item in imported if item.get("document_id")
            }
            succeeded = sum(item["status"] == "success" for item in file_results)
            failed = len(file_results) - succeeded
            response_body = {
                "imported": imported,
                "files": file_results,
                "summary": {
                    "selected_file_count": len(file_results),
                    "succeeded_file_count": succeeded,
                    "failed_file_count": failed,
                    "imported_document_count": len(imported),
                    "new_document_count": sum(
                        not item.get("duplicate", False) and not item.get("reindexed", False)
                        for item in imported
                    ),
                    "reindexed_document_count": sum(
                        bool(item.get("reindexed")) for item in imported
                    ),
                    "duplicate_document_count": sum(bool(item.get("duplicate")) for item in imported),
                    "auto_split_file_count": sum(bool(item.get("auto_split")) for item in file_results),
                    "semantic_split_file_count": sum(
                        bool(item.get("auto_split"))
                        and item.get("requested_split_strategy") == "semantic"
                        for item in file_results
                    ),
                    "split_document_count": sum(int(item.get("split_part_count") or 0) for item in file_results),
                    "chunk_count": sum(int(item.get("chunk_count") or 0) for item in imported),
                    "new_chunk_count": sum(
                        int(item.get("chunk_count") or 0)
                        for item in unique_imported.values()
                        if not item.get("duplicate", False) and not item.get("reindexed", False)
                    ),
                    "reindexed_chunk_count": sum(
                        int(item.get("chunk_count") or 0)
                        for item in unique_imported.values()
                        if item.get("reindexed", False)
                    ),
                    "linked_chunk_count": sum(
                        int((item.get("storm_linking") or {}).get("linked_chunk_count") or 0)
                        for item in unique_imported.values()
                    ),
                    "unlinked_chunk_count": sum(
                        int((item.get("storm_linking") or {}).get("unlinked_chunk_count") or 0)
                        for item in unique_imported.values()
                    ),
                    "auto_accepted_link_count": sum(
                        int((item.get("storm_linking") or {}).get("auto_accepted_count") or 0)
                        for item in unique_imported.values()
                    ),
                    "needs_review_link_count": sum(
                        int((item.get("storm_linking") or {}).get("needs_review_count") or 0)
                        for item in unique_imported.values()
                    ),
                    "structured_storm_surge_file_count": structured_import["event_count"],
                    "structured_storm_surge_linked_event_count": structured_import["linked_event_count"],
                    "structured_storm_surge_high_confidence_count": structured_import["high_confidence_links"],
                    "structured_storm_surge_time_overlap_count": structured_import["time_overlap_links"],
                    "structured_storm_surge_station_count": structured_import["station_count"],
                    "structured_storm_surge_observation_count": structured_import["observation_count"],
                    "legacy_doc_file_count": sum(
                        int(item.get("legacy_doc_event_count") or 0) > 0 for item in file_results
                    ),
                    "legacy_doc_event_count": sum(
                        int(item.get("legacy_doc_event_count") or 0) for item in file_results
                    ),
                    "legacy_doc_high_confidence_count": sum(
                        int(item.get("legacy_doc_high_confidence_count") or 0) for item in file_results
                    ),
                    "legacy_doc_needs_review_count": sum(
                        int(item.get("legacy_doc_needs_review_count") or 0) for item in file_results
                    ),
                    "legacy_doc_unlinked_count": sum(
                        int(item.get("legacy_doc_unlinked_count") or 0) for item in file_results
                    ),
                },
                "status": components.rag.status(),
            }
            if not succeeded:
                response_body.update(
                    {
                        "error": "所选文件均未能导入，请查看 files 中的逐文件错误",
                        "code": "KNOWLEDGE_IMPORT_FAILED",
                    }
                )
                return jsonify(response_body), 400
            return jsonify(response_body)
        except KnowledgeImportError as exc:
            return jsonify({"error": str(exc), "code": "KNOWLEDGE_IMPORT_FAILED"}), 400
        except Exception as exc:
            print(f"[Knowledge Import Error] {exc}")
            return jsonify({"error": f"知识导入失败: {exc}", "code": "INTERNAL_ERROR"}), 500

    @blueprint.post("/knowledge/search")
    def knowledge_search():
        body = request.get_json(silent=True) or {}
        query = str(body.get("query") or "").strip()
        if not query:
            return jsonify({"error": "检索词不能为空", "code": "INVALID_REQUEST"}), 400
        return jsonify({"query": query, "results": components.rag.search(query, limit=body.get("limit", 8))})

    @blueprint.post("/knowledge/relink")
    def knowledge_relink():
        """Re-run deterministic SID linking after a catalogue/data update."""

        result = components.rag.refresh_storm_links()
        if not result.get("available"):
            return jsonify({"code": "STORM_LINKING_UNAVAILABLE", **result}), 503
        return jsonify({"result": result, "status": components.rag.status()})

    @blueprint.delete("/knowledge/documents/<document_id>")
    def knowledge_delete(document_id: str):
        if components.rag.delete_document(document_id):
            return jsonify({"deleted": True, "document_id": document_id})
        return jsonify({"error": "知识文档不存在", "code": "NOT_FOUND"}), 404

    @blueprint.get("/<sid>")
    def detail(sid: str):
        try:
            storm = components.repositories.historical.get_storm(sid, include_track=True)
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc), "code": "DATA_NOT_READY"}), 503
        if storm is None:
            return jsonify({"error": "typhoon not found", "code": "NOT_FOUND"}), 404
        storm["storm_surge"] = components.repositories.storm_surge.assess(
            storm,
            storm.get("track", []),
        )
        return jsonify(storm)

    return blueprint
