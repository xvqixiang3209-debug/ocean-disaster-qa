import json
from pathlib import Path
import tempfile
import unittest

from flask import Flask

from marine_kb.api import create_blueprint
from marine_kb.requirements import load_catalog, status_summary
from marine_kb.storage import KnowledgeStore


class EmptyIngestion:
    root = Path(tempfile.gettempdir())


class DisabledQA:
    def model_status(self):
        return {"state": "disabled", "enabled": False, "configured": False,
                "tested": False, "note": "disabled"}

    def test_model_connection(self):
        raise RuntimeError("外部模型开关未启用")


class MonthOneCatalogTests(unittest.TestCase):
    def test_shipped_catalog_has_160_questions_and_ten_categories(self):
        path = Path(__file__).resolve().parents[1] / "data" / "requirements" / "catalog.json"
        catalog = load_catalog(path)
        self.assertTrue(catalog["available"])
        self.assertEqual(catalog["question_count"], 160)
        self.assertEqual(len(catalog["categories"]), 10)
        self.assertEqual(len(catalog["items"]), 160)
        summary = status_summary(catalog["items"])
        self.assertEqual(summary["month_one_question_count"], 77)
        self.assertEqual(summary["passed_count"], 0)
        self.assertEqual(summary["status_counts"]["pending_confirmation"], 160)

    def test_requirements_endpoint_is_read_only_and_filterable(self):
        with tempfile.TemporaryDirectory() as temp:
            store = KnowledgeStore(Path(temp) / "knowledge.sqlite3")
            app = Flask(__name__)
            app.register_blueprint(create_blueprint(store, EmptyIngestion(), DisabledQA()))
            client = app.test_client()
            status = client.get("/api/knowledge/status")
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json["requirements"]["question_count"], 160)
            self.assertEqual(status.json["external_model"]["state"], "disabled")
            self.assertEqual(status.json["requirements"]["scope_counts"],
                             {"professional": 64, "shared": 60, "public_later": 36})
            public = client.get('/api/knowledge/requirements?scope=public_later&limit=100').json
            self.assertEqual(public['total'], 36)
            self.assertTrue(all('公众' in item['role'] for item in public['items']))
            professional = client.get('/api/knowledge/requirements?role=B%20专业技术人员').json
            self.assertEqual(professional['total'], 32)
            response = client.get("/api/knowledge/requirements?category=B01&month=第1个月&limit=100")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["total"], 11)
            self.assertTrue(all(item["category_id"] == "B01" for item in response.json["items"]))
            self.assertEqual(client.get("/api/knowledge/inventory").status_code, 200)
            self.assertEqual(client.post("/api/knowledge/requirements").status_code, 405)
            token = status.json["csrf_token"]
            question_id = response.json["items"][0]["id"]
            rejected = client.post(f"/api/knowledge/requirements/{question_id}/state", json={"status": "answerable", "note": "依据已核对"})
            self.assertEqual(rejected.status_code, 403)
            saved = client.post(f"/api/knowledge/requirements/{question_id}/state", headers={"X-Knowledge-Token": token},
                                json={"status": "awaiting_data", "note": "缺少经确认的资料和接口"})
            self.assertEqual(saved.status_code, 200)
            refreshed = client.get("/api/knowledge/requirements?category=B01&month=第1个月&limit=100")
            updated = next(item for item in refreshed.json["items"] if item["id"] == question_id)
            self.assertEqual(updated["status"], "awaiting_data")
            history = client.get(f'/api/knowledge/requirements/{question_id}/history').json['items']
            self.assertEqual(history[0]['previous_status'], 'pending_confirmation')
            self.assertEqual(history[0]['status'], 'awaiting_data')
            self.assertEqual(refreshed.json["summary"]["passed_count"], 0)
            model_test = client.post("/api/knowledge/model-test", headers={"X-Knowledge-Token": token})
            self.assertEqual(model_test.status_code, 503)
            self.assertEqual(model_test.json["state"], "disabled")


if __name__ == "__main__":
    unittest.main()
