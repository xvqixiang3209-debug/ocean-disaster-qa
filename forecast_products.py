"""Read-only, traceable catalogue of locally imported forecast products.

Only explicit document text/metadata supplies issue times, forecast periods,
publishers and product roles. No filename date becomes an issue time. Relative
forecast periods stay relative and an unstated timezone stays unknown. All
records remain local archives: this module has no network client or DB writes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
from typing import Any


FORECAST_CATEGORIES = frozenset({"预报警报", "预报产品", "海浪预报", "风暴潮预报", "模式结果"})
_DATE = r"(?P<year>20\d{2})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日(?:\s*(?P<hour>\d{1,2})\s*(?:时|[:：])(?:\s*(?P<minute>\d{1,2})\s*分?)?)?"
_ISSUE_TIME = re.compile(r"(?:发布时间|发布于|发报时间|预报时间|时间)\s*[:：]?\s*" + _DATE)
_VALID_TEXT = re.compile(r"(?:预报时段|有效时段|有效期|预报有效期)\s*[:：]\s*([^。；\n]{2,100})")
_RELATIVE_PERIOD = re.compile(r"预计\s*((?:(?:今天|今夜|明天|明日|后天|未来\s*\d+\s*小时|\d{1,2}日)[^：:，,；。\n]{0,35}))(?:[:：，,])")
_REGIONS = ("浙江北部沿岸海域", "浙江中部沿岸海域", "浙江南部沿岸海域", "浙江沿岸海域", "浙江北部海域", "浙江中部海域", "浙江南部海域", "杭州湾", "象山港", "三门湾", "乐清湾", "温州", "舟山", "宁波", "台州", "嘉兴", "东海", "黄海", "渤海", "台湾海峡", "南海")


def _json(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}") if not isinstance(value, dict) else value
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.isdigit() and len(text) in (8, 10, 12, 14):
            return datetime.strptime(text.ljust(14, "0"), "%Y%m%d%H%M%S")
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _time_payload(value: datetime | None = None, *, origin=None, text=None, locator=None):
    return {
        "value": value.isoformat().replace("+00:00", "Z") if value else None,
        "known": value is not None,
        "origin": origin,
        "timezone": ("UTC" if value and value.utcoffset() is not None and value.utcoffset().total_seconds() == 0 else str(value.tzinfo)) if value and value.tzinfo else "unknown",
        "text": text,
        "locator": locator,
    }


def _explicit_metadata_time(metadata, keys):
    for key in keys:
        value = _parse_time(metadata.get(key))
        if value:
            return _time_payload(value, origin=f"metadata.{key}", text=str(metadata[key]))
    for name in ("product", "forecast", "temporal", "times"):
        nested = metadata.get(name)
        if isinstance(nested, dict):
            result = _explicit_metadata_time(nested, keys)
            if result["known"]:
                result["origin"] = f"{name}.{result['origin']}"
                return result
    return _time_payload()


def _document_fields(chunks):
    issue = _time_payload()
    valid = _time_payload()
    publisher = {"value": None, "text": None, "locator": None, "known": False}
    signatory = {"value": None, "text": None, "locator": None, "known": False}
    regions, roles = [], []
    for chunk in chunks:
        text, locator = chunk["text"].strip(), chunk["locator"]
        # An issue timestamp is accepted only next to an explicit time label.
        match = _ISSUE_TIME.search(text)
        if match and not issue["known"]:
            values = match.groupdict()
            try:
                stamp = datetime(int(values["year"]), int(values["month"]), int(values["day"]), int(values["hour"] or 0), int(values["minute"] or 0))
                issue = _time_payload(stamp, origin="document_header", text=match.group(0), locator=locator)
                issue["precision"] = "minute" if values["minute"] else "hour" if values["hour"] else "date"
                if not values["hour"]:
                    issue["value"] = stamp.date().isoformat()
            except ValueError:
                pass
        if not valid["text"]:
            period = _VALID_TEXT.search(text) or _RELATIVE_PERIOD.search(text)
            if period:
                valid = _time_payload(origin="document_text", text=period.group(1).strip(), locator=locator)
                valid["relative_or_unparsed"] = True
        if not publisher["known"]:
            name = re.search(r"([\u3400-\u9fff]{2,30}(?:海洋监测预报中心|海洋预报台|海洋环境预报中心))", text[:120])
            if name:
                publisher = {"value": name.group(1), "text": text[:160], "locator": locator, "known": True}
        if not signatory["known"]:
            signed = re.search(r"签发\s*[:：]\s*([\u3400-\u9fff]{2,4})(?=\s|$)", text)
            if signed:
                signatory = {"value": signed.group(1), "text": signed.group(0), "locator": locator, "known": True}
        if not text.startswith(("报：", "抄报：", "抄送：", "发往：", "电话：")):
            for region in _REGIONS:
                if region in text and not any(r["name"] == region for r in regions):
                    regions.append({"name": region, "text": text[:600], "locator": locator})
        if re.search(r"(?:预计|将出现|海浪预报|风暴潮预报|预报(?:时段|有效|内容|范围))", text) and not any(r["role"] == "forecast" for r in roles):
            roles.append({"role": "forecast", "text": text[:600], "locator": locator})
        if re.search(r"(?:实测|实况|观测到)", text) and not any(r["role"] == "observed" for r in roles):
            roles.append({"role": "observed", "text": text[:600], "locator": locator})
        if re.search(r"(?:发布.*警报|警报解除通报|海浪.*警报|风暴潮.*警报)", text) and not any(r["role"] == "warning" for r in roles):
            roles.append({"role": "warning", "text": text[:600], "locator": locator})
    return issue, valid, publisher, signatory, regions, roles


class ForecastProductCatalog:
    def __init__(self, database: str | Path, *, now: datetime | None = None):
        self.database = Path(database).resolve()
        self.now = now or datetime.now(timezone.utc)

    def _connect(self):
        db = sqlite3.connect(f"file:{self.database.as_posix()}?mode=ro", uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _filter(query="", region="", include_pending=False):
        # The parent folder also contains standards and response letters;
        # those are not forecast products and must not enter this catalogue.
        clauses = ["s.category IN ({})".format(",".join("?" * len(FORECAST_CATEGORIES)))]
        params = [*FORECAST_CATEGORIES]
        if not include_pending:
            clauses.append("s.status IN ('approved','indexed')")
        for value, filename in ((query, True), (region, False)):
            if value:
                term = "%" + str(value)[:120].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                fields = ["EXISTS (SELECT 1 FROM chunks c WHERE c.source_id=s.source_id AND c.text LIKE ? ESCAPE '\\')"]
                params.append(term)
                if filename:
                    fields += ["s.filename LIKE ? ESCAPE '\\'", "s.relative_path LIKE ? ESCAPE '\\'"]
                    params += [term, term]
                clauses.append("(" + " OR ".join(fields) + ")")
        return " AND ".join(clauses), params

    def _product(self, db, row):
        chunks = [dict(r) for r in db.execute("SELECT locator,text FROM chunks WHERE source_id=? ORDER BY chunk_id LIMIT 60", (row["source_id"],))]
        metadata = _json(row["metadata"])
        issue, valid, publisher, signatory, regions, roles = _document_fields(chunks)
        explicit_issue = _explicit_metadata_time(metadata, ("issue_time", "issued_at", "issued_time", "product_time"))
        if explicit_issue["known"]:
            issue = explicit_issue
        explicit_valid = _explicit_metadata_time(metadata, ("valid_time", "valid_at", "valid_until", "valid_from"))
        if explicit_valid["known"]:
            valid = explicit_valid
        role_names = [r["role"] for r in roles]
        explicit_role = metadata.get("observed_or_forecast") or metadata.get("data_role")
        role = str(explicit_role) if explicit_role else "+".join(role_names) if role_names else "unknown"
        parsed = _parse_time(issue["value"])
        freshness = "historical" if parsed and parsed.year < self.now.year else "stale_local_archive" if parsed and parsed.date() < self.now.date() else "unknown"
        try:
            year = int(metadata["event_year"]) if metadata.get("event_year") is not None else None
        except (TypeError, ValueError):
            year = None
        return {
            "source_id": row["source_id"], "filename": row["filename"], "relative_path": row["relative_path"],
            "category": row["category"], "review_status": row["status"], "eligible_for_answer": row["status"] in {"approved", "indexed"},
            "chunk_count": row["chunk_count"], "event_year": year,
            "issue_time": issue, "valid_time": valid, "publisher": publisher, "signatory": signatory,
            "regions": regions, "data_role": role, "role_evidence": roles,
            "freshness": freshness, "availability": "local_archive", "live_source": False,
            "source_url": f"/api/knowledge/sources/{row['source_id']}",
            "original_url": f"/api/knowledge/sources/{row['source_id']}/file",
            "excerpt": "\n".join(chunk["text"] for chunk in chunks[:10])[:2500],
        }

    def query(self, *, query="", date="", region="", page=1, limit=50, include_pending=False):
        page, limit = max(1, int(page)), max(1, min(int(limit), 100))
        where, params = self._filter(query, region, include_pending)
        db = self._connect()
        try:
            # Date filtering is against explicit issue dates after extraction;
            # filename dates and file import times do not qualify.
            if date:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                    raise ValueError("date must use YYYY-MM-DD")
                datetime.strptime(date, "%Y-%m-%d")
                rows = db.execute(f"SELECT s.* FROM sources s WHERE {where} ORDER BY s.updated_at DESC,s.source_id", params).fetchall()
                products = [self._product(db, row) for row in rows]
                products = [p for p in products if str(p["issue_time"]["value"] or "").startswith(date)]
                return {"items": products[(page-1)*limit:page*limit], "total": len(products), "page": page, "page_size": limit}
            total = db.execute(f"SELECT count(*) FROM sources s WHERE {where}", params).fetchone()[0]
            rows = db.execute(f"SELECT s.* FROM sources s WHERE {where} ORDER BY s.updated_at DESC,s.source_id LIMIT ? OFFSET ?", (*params, limit, (page-1)*limit)).fetchall()
            return {"items": [self._product(db, row) for row in rows], "total": total, "page": page, "page_size": limit}
        finally:
            db.close()

    def list(self, **kwargs):
        return self.query(**kwargs)["items"]

    def count(self, **kwargs):
        return self.query(limit=1, **kwargs)["total"]

    def get(self, source_id, *, include_pending=False):
        where, params = self._filter(include_pending=include_pending)
        db = self._connect()
        try:
            row = db.execute(f"SELECT s.* FROM sources s WHERE s.source_id=? AND {where}", (source_id, *params)).fetchone()
            return self._product(db, row) if row else None
        finally:
            db.close()

    def status(self):
        return {"available": bool(self.count()), "live_source": False, "availability": "local_archive_only", "note": "仅查询本地导入的历史产品；未连接实时预报接口。", "source_count": self.count()}


def create_blueprint(database_getter):
    """Private read-only API with the knowledge workbench's loopback boundary."""
    from flask import Blueprint, jsonify, request
    bp = Blueprint("forecast_products", __name__, url_prefix="/api/forecast-products")

    @bp.before_request
    def local_boundary():
        try:
            local = ipaddress.ip_address(request.remote_addr or "").is_loopback
        except ValueError:
            local = False
        host = urlsplit(request.host_url).hostname
        if not local or host not in {"127.0.0.1", "localhost", "::1"}:
            return jsonify(error="预报资料工作台仅允许本机访问，请使用127.0.0.1或localhost"), 403
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            return jsonify(error="拒绝跨站请求"), 403

    @bp.after_request
    def private(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @bp.get("")
    def products():
        try:
            page, limit = int(request.args.get("page", 1)), int(request.args.get("limit", 30))
            if page < 1 or limit < 1:
                raise ValueError()
            result = ForecastProductCatalog(database_getter()).query(query=request.args.get("q", "")[:120], date=request.args.get("date", ""), region=request.args.get("region", "")[:60], page=page, limit=limit)
            return jsonify(**result, availability="local_archive_only", live_source=False, note="历史产品查询；不是当前预报。时间/签发/有效期未记载时明确标为未知。")
        except ValueError:
            return jsonify(error="页码、条数或日期无效；日期格式应为YYYY-MM-DD"), 400
        except (sqlite3.Error, OSError):
            return jsonify(error="本地产品目录暂不可用"), 503

    @bp.get("/<source_id>")
    def product(source_id):
        try:
            item = ForecastProductCatalog(database_getter()).get(source_id)
            return jsonify(item) if item else (jsonify(error="预报资料不存在或尚未通过检索门禁"), 404)
        except (sqlite3.Error, OSError):
            return jsonify(error="本地产品目录暂不可用"), 503
    return bp


__all__ = ["ForecastProductCatalog", "create_blueprint", "FORECAST_CATEGORIES"]
