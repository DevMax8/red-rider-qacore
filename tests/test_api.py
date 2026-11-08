"""HTTP flows using a fake model; no external model requests or paid calls."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dotenv import dotenv_values
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain import CriterionResult, ModelInfo
from app.main import create_app
from app.providers import ProviderError


class FakeEvaluator:
    model_info = ModelInfo(mode="local", name="unit-test-model")

    def __init__(self, fail_id=None):
        self.fail_id = fail_id

    async def evaluate(self, conversation, checklist):
        if conversation.conversation_id == self.fail_id:
            raise ProviderError("Test model failure")
        return [CriterionResult(
            criterion_id=config.criterion_id,
            value=(False if config.criterion_id == "critical" else
                   None if config.allow_na and config.criterion_id in {"discovery", "identification"} else
                   True if config.answer_type == "boolean" else config.scale[-1]),
            rationale="Synthetic test evaluation", evidence_turns=[1])
            for config in checklist.criteria]


def envelope(count=10, group_id="CONTROL"):
    return {"error": "", "isSuccess": True, "data": {
        "dataset_group_id": group_id, "total_records": count,
        "records": [{"conversation_id": f"source-{index}",
                     "channel": "voice call" if group_id == "DEV" and count == 65 and index >= 60 else "agent chat",
                     "language": "RU",
                     "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Здравствуйте, где тарифы?"},
                                    {"timestamp": "00:01", "speaker": "operator", "text": "Здравствуйте, меня зовут Алия. Тарифы на сайте. До свидания."}],
                     "audio_url": None} for index in range(count)]}}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.settings = Settings(data_dir=base / "data", env_file=base / ".env", model_name="unit-test-model")
        self.app = create_app(self.settings, FakeEvaluator())
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def import_data(self, count=10, group_id="CONTROL"):
        response = self.client.post("/api/datasets/import", json={"payload": envelope(count, group_id)})
        self.assertEqual(response.status_code, 201, response.text)

    def evaluate_data(self, group_id="CONTROL"):
        response = self.client.post("/api/evaluations", json={"group_id": group_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_end_to_end_import_evaluate_export_preserves_na(self):
        self.import_data()
        self.assertEqual(self.evaluate_data()["completed"], 10)
        summary = self.client.get("/api/conversations", params={"group_id": "CONTROL"}).json()
        self.assertEqual(summary[0]["final_score"], 100)
        self.assertEqual(summary[0]["status"], "ok")
        response = self.client.get("/api/results/export", params={"group_id": "CONTROL", "subset": "prod"})
        self.assertEqual(response.status_code, 200, response.text)
        bundle = response.json()
        self.assertEqual(bundle["model"]["name"], "unit-test-model")
        self.assertEqual(len(bundle["results"]), 10)
        result = bundle["results"][0]
        self.assertNotIn("error", result)
        criterion = next(c for c in result["criteria"] if c["criterion_id"] == "discovery")
        self.assertIn("value", criterion)
        self.assertIsNone(criterion["value"])
        self.assertEqual(criterion["evidence_turns"], [1])

    def test_appeal_changes_final_and_preserves_original(self):
        self.import_data()
        self.evaluate_data()
        response = self.client.post("/api/conversations/source-0/appeals", json={
            "group_id": "CONTROL", "criterion_id": "contact", "comment": "Проверить приветствие"})
        self.assertEqual(response.status_code, 201, response.text)
        appeal_id = response.json()["id"]
        self.assertEqual(len(self.client.get("/api/appeals", params={"group_id": "CONTROL"}).json()), 1)
        decision = {"decision": "change", "reason": "Нет представления", "replacement": {
            "criterion_id": "contact", "value": False, "rationale": "Нет представления", "evidence_turns": [2]}}
        response = self.client.post(f"/api/appeals/{appeal_id}/decision", json=decision)
        self.assertEqual(response.status_code, 200, response.text)
        record = response.json()["record"]
        self.assertEqual(record["original_result"]["score"]["final_score"], 100)
        self.assertLess(record["final_result"]["score"]["final_score"], 100)
        self.assertEqual(self.client.post(f"/api/appeals/{appeal_id}/decision", json=decision).status_code, 409)

    def test_checklist_update_is_versioned_and_historical_snapshot_kept(self):
        self.import_data()
        self.evaluate_data()
        checklist = self.client.get("/api/checklists/default").json()
        checklist["criteria"][0]["description"] = "Новая формулировка"
        response = self.client.put("/api/checklists/default", json=checklist)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["version"], 2)
        self.assertEqual(self.client.put("/api/checklists/default", json=checklist).status_code, 409)
        historical = self.client.get("/api/conversations/source-0", params={"group_id": "CONTROL"}).json()
        self.assertEqual(historical["checklist"]["version"], 1)

    def test_provider_failure_is_preserved_as_error_record(self):
        self.app.state.service.evaluator = FakeEvaluator(fail_id="source-3")
        self.import_data()
        summary = self.evaluate_data()
        self.assertEqual(summary["failed"], 1)
        response = self.client.get("/api/results/export", params={"group_id": "CONTROL", "subset": "prod"})
        self.assertEqual(response.status_code, 200, response.text)
        failed = next(r for r in response.json()["results"] if r["conversation_id"] == "source-3")
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["criteria"], [])
        self.assertIsNone(failed["score"])

    def test_unconfigured_model_returns_503_and_no_fake_result(self):
        base = Path(self.directory.name)
        app = create_app(Settings(data_dir=base / "unconfigured", env_file=base / "unconfigured.env"))
        with TestClient(app) as client:
            client.post("/api/datasets/import", json={"payload": envelope()})
            response = client.post("/api/evaluations", json={"group_id": "CONTROL"})
            self.assertEqual(response.status_code, 503, response.text)
            record = client.get("/api/conversations/source-0", params={"group_id": "CONTROL"}).json()
            self.assertIsNone(record["final_result"])

    def test_demo_is_explicit_synthetic_and_cannot_be_exported(self):
        response = self.client.post("/api/demo")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(response.json()["demo"])
        self.assertEqual(response.json()["total_records"], 3)
        records = self.client.get("/api/conversations", params={"group_id": "DEMO"}).json()
        self.assertEqual(len(records), 3)
        self.assertTrue(all(row["demo"] for row in records))
        self.assertTrue(any(row["critical_flag"] for row in records))
        detail = self.client.get("/api/conversations/demo-ru-001", params={"group_id": "DEMO"}).json()
        self.assertIn("без ИИ", detail["model"]["name"])
        self.assertEqual(self.client.get("/api/results/export", params={"group_id": "DEMO", "subset": "prod"}).status_code, 422)
        self.assertEqual(self.client.post("/api/datasets/import", json={"payload": envelope(3, "DEMO")}).status_code, 422)

    def test_settings_redacts_secrets_persists_literals_and_reconfigures(self):
        secret = "test-secret-${LITERAL}"
        url = "https://example.api.powerplatform.com/test?sig=fake-secret&group_id=DEV"
        response = self.client.put("/api/settings", json={"model_name": "actual-model",
            "model_mode": "local", "model_base_url": "http://127.0.0.1:11434/v1",
            "model_api_key": secret, "dataset_api_url": url, "team_name": "Команда"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(secret, response.text)
        self.assertNotIn(url, response.text)
        self.assertTrue(response.json()["api_key_configured"])
        self.assertTrue(response.json()["dataset_api_configured"])
        self.assertEqual(self.app.state.service.evaluator.model_info.name, "actual-model")
        stored = dotenv_values(self.settings.env_file, interpolate=False)
        self.assertEqual(stored["MODEL_API_KEY"], secret)
        self.assertEqual(stored["DATASET_API_URL"], url)
        self.client.put("/api/settings", json={"model_api_key": "", "dataset_api_url": ""})
        self.assertTrue(self.client.get("/api/settings").json()["api_key_configured"])
        response = self.client.put("/api/settings", json={"clear_api_key": True, "clear_dataset_api_url": True})
        self.assertFalse(response.json()["api_key_configured"])
        self.assertFalse(response.json()["dataset_api_configured"])

    def test_settings_change_rejected_during_evaluation(self):
        self.app.state.service._inflight.add(("CONTROL", "source-1"))
        response = self.client.put("/api/settings", json={"model_name": "new-model"})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.settings.env_file.exists())

    def test_validation_does_not_echo_private_input(self):
        response = self.client.post("/api/datasets/import", json={"payload": [], "unexpected": "private-secret"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("private-secret", response.text)
        response = self.client.put("/api/settings", json={"model_api_key": "private-secret\nline"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("private-secret", response.text)

    def test_settings_validate_ports_and_finite_numbers(self):
        for url in ("http://localhost:bad/v1", "http://localhost:70000/v1"):
            response = self.client.put("/api/settings", json={"model_base_url": url})
            self.assertEqual(response.status_code, 422)
        response = self.client.put("/api/settings", json={"dataset_api_url": "https://example.api.powerplatform.com:bad/path?sig=secret"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("sig=secret", response.text)
        for value in (float("nan"), float("inf"), -1, 91):
            with self.assertRaises(ValueError):
                Settings(model_timeout=value)

    def test_settings_roundtrip_windows_paths_and_literal_secret_characters(self):
        env_path = Path(self.directory.name) / "windows.env"
        windows_path = Path(r"C:\Users\Dell\My Projects\red_rider\data")
        secret = r'key-${SHOULD_STAY_LITERAL}-$-quote"-backslash\end'
        settings = Settings(data_dir=windows_path, env_file=env_path, model_api_key=secret)
        settings.persist()
        loaded = dotenv_values(env_path, interpolate=False)
        self.assertEqual(loaded["DATA_DIR"], str(windows_path))
        self.assertEqual(loaded["MODEL_API_KEY"], secret)

    def test_save_results_writes_fixed_repository_paths_and_blocks_demo(self):
        root = self.settings.env_file.parent
        self.client.post("/api/demo")
        blocked = self.client.post("/api/results/save", json={"group_id": "DEMO", "subset": "prod"})
        self.assertEqual(blocked.status_code, 422)
        self.assertFalse((root / "results" / "prod" / "results.json").exists())
        for group_id, subset, count in (("CONTROL", "prod", 10), ("DEV", "dev", 65)):
            self.import_data(count, group_id)
            self.evaluate_data(group_id)
            response = self.client.post("/api/results/save", json={"group_id": group_id, "subset": subset})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"saved": True, "path": f"results/{subset}/results.json", "records": count})
            stored = json.loads((root / "results" / subset / "results.json").read_text(encoding="utf-8"))
            self.assertEqual(len(stored["results"]), count)
            self.assertEqual(stored["model"]["name"], "unit-test-model")
        invalid = self.client.post("/api/results/save", json={"group_id": "CONTROL", "subset": "../../outside"})
        self.assertEqual(invalid.status_code, 422)

    def test_duplicate_dataset_data_and_missing_ids_rejected(self):
        self.import_data()
        duplicate = envelope()
        duplicate["data"]["records"][0]["transcript"][0]["text"] = "Изменено"
        self.assertEqual(self.client.post("/api/datasets/import", json={"payload": duplicate}).status_code, 409)
        self.assertEqual(self.client.get("/api/conversations/missing", params={"group_id": "CONTROL"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
