import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from forecast_products import ForecastProductCatalog, create_blueprint


class ForecastProductCatalogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "knowledge.sqlite3"
        db = sqlite3.connect(self.db_path)
        db.executescript("""
            CREATE TABLE sources (
                source_id TEXT PRIMARY KEY, filename TEXT, relative_path TEXT,
                sha256 TEXT, bytes INTEGER, raw_path TEXT, category TEXT,
                status TEXT, quality_status TEXT, metadata TEXT, warnings TEXT,
                created_at TEXT, updated_at TEXT, chunk_count INTEGER
            );
            CREATE TABLE chunks (
                chunk_id INTEGER PRIMARY KEY, source_id TEXT, locator TEXT,
                text TEXT, metadata TEXT
            );
        """)
        indexed_meta = {"event_year": 2018, "issue_time": "2018-10-06T10:00:00Z"}
        db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "s1", "wj2018100610.doc", "预报相关资料/预报单/wj2018100610.doc", "x", 1,
            "D:/secret/raw", "预报警报", "indexed", "ok", json.dumps(indexed_meta), "[]",
            "2018-10-06T10:00:00Z", "2018-10-06T10:00:00Z", 1,
        ))
        db.execute("INSERT INTO chunks VALUES(?,?,?,?,?)", (1, "s1", "第1页", "台风预报：预计浙江沿海将出现风暴潮过程。", "{}"))
        pending_meta = {"event_year": 2024}
        db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "s2", "2024海浪预报.pdf", "预报相关资料/2024海浪预报.pdf", "y", 1,
            "D:/secret/raw", "预报警报", "pending_review", "review", json.dumps(pending_meta), "[]",
            "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z", 1,
        ))
        db.execute("INSERT INTO chunks VALUES(?,?,?,?,?)", (2, "s2", "第1页", "待审核资料。", "{}"))
        db.commit(); db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_indexed_product_has_traceable_temporal_and_local_labels(self):
        catalog = ForecastProductCatalog(self.db_path, now=datetime(2026, 10, 15, tzinfo=timezone.utc))
        products = catalog.list(query="风暴潮")
        self.assertEqual(len(products), 1)
        item = products[0]
        self.assertEqual(item["source_id"], "s1")
        self.assertEqual(item["issue_time"]["value"], "2018-10-06T10:00:00Z")
        self.assertEqual(item["valid_time"]["known"], False)
        self.assertEqual(item["freshness"], "historical")
        self.assertFalse(item["live_source"])
        self.assertTrue(item["eligible_for_answer"])
        self.assertIn("forecast", item["data_role"])
        self.assertNotIn("raw", item)
        self.assertNotIn("D:/secret", json.dumps(item, ensure_ascii=False))

    def test_pending_is_excluded_by_default_and_explicitly_labeled_when_included(self):
        catalog = ForecastProductCatalog(self.db_path)
        self.assertEqual(len(catalog.list()), 1)
        items = catalog.list(include_pending=True)
        self.assertEqual(len(items), 2)
        pending = next(item for item in items if item["source_id"] == "s2")
        self.assertEqual(pending["review_status"], "pending_review")
        self.assertFalse(pending["eligible_for_answer"])
        self.assertEqual(pending["issue_time"]["known"], False)
        self.assertEqual(pending["freshness"], "unknown")
        self.assertIsNone(catalog.get("s2"))

    def test_status_explicitly_says_local_archive_only(self):
        status = ForecastProductCatalog(self.db_path).status()
        self.assertTrue(status["available"])
        self.assertFalse(status["live_source"])
        self.assertEqual(status["availability"], "local_archive_only")

    def test_get_missing_is_none(self):
        self.assertIsNone(ForecastProductCatalog(self.db_path).get("does-not-exist"))

    def test_filename_date_is_not_assumed_to_be_issue_or_valid_time(self):
        db = sqlite3.connect(self.db_path)
        db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "s3", "2019100610海浪预报.pdf", "预报相关资料/2019100610海浪预报.pdf", "z", 1,
            "D:/secret/raw", "预报警报", "indexed", "ok", "{}", "[]",
            "2019-10-06T10:00:00Z", "2019-10-06T10:00:00Z", 0,
        ))
        db.commit(); db.close()
        item = ForecastProductCatalog(self.db_path).get("s3")
        self.assertFalse(item["issue_time"]["known"])
        self.assertFalse(item["valid_time"]["known"])
        self.assertEqual(item["freshness"], "unknown")

    def test_explicit_document_headers_and_relative_period_keep_evidence_and_timezone(self):
        db = sqlite3.connect(self.db_path)
        db.execute("UPDATE sources SET metadata='{}' WHERE source_id='s1'")
        db.executemany("INSERT INTO chunks VALUES(?,?,?,?,?)", [
            (3, "s1", "paragraph:1", "浙江省海洋监测预报中心 橙色", "{}"),
            (4, "s1", "paragraph:3", "时间：2018年10月6日10时", "{}"),
            (5, "s1", "paragraph:4", "编号：海浪1825-09 签发：", "{}"),
            (6, "s1", "paragraph:8", "预计今天夜间到明天白天：杭州湾有2.5米到3.5米的大浪。", "{}"),
        ])
        db.commit(); db.close()
        catalog = ForecastProductCatalog(self.db_path)
        item = catalog.get("s1")
        self.assertEqual(item["issue_time"]["value"], "2018-10-06T10:00:00")
        self.assertEqual(item["issue_time"]["locator"], "paragraph:3")
        self.assertEqual(item["issue_time"]["timezone"], "unknown")
        self.assertEqual(item["publisher"]["value"], "浙江省海洋监测预报中心")
        self.assertFalse(item["signatory"]["known"])
        self.assertFalse(item["valid_time"]["known"])
        self.assertEqual(item["valid_time"]["text"], "今天夜间到明天白天")
        self.assertEqual(item["valid_time"]["locator"], "paragraph:8")
        self.assertIn("杭州湾", [r["name"] for r in item["regions"]])
        self.assertEqual(catalog.query(date="2018-10-06", region="杭州湾")["total"], 1)
        self.assertEqual(catalog.query(date="2018-10-07")["total"], 0)

    def test_api_local_boundary_cache_gate_and_invalid_filters(self):
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(create_blueprint(lambda: self.db_path))
        client = app.test_client()
        result = client.get("/api/forecast-products")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        self.assertFalse(result.json["live_source"])
        self.assertEqual(result.json["total"], 1)
        self.assertEqual(client.get("/api/forecast-products/s2").status_code, 404)
        self.assertEqual(client.get("/api/forecast-products", environ_base={"REMOTE_ADDR": "10.1.2.3"}).status_code, 403)
        self.assertEqual(client.get("/api/forecast-products", base_url="http://evil.example").status_code, 403)
        self.assertEqual(client.get("/api/forecast-products?date=not-a-date").status_code, 400)
        self.assertEqual(client.get("/api/forecast-products?page=0").status_code, 400)


if __name__ == "__main__":
    unittest.main()
