"""Exercise the actual CLI import -> evaluate -> export -> validate workflow."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import cli
from app.config import Settings
from app.domain import CriterionResult, ModelInfo
from app.providers import ProviderError
from app.service import EvaluationService
from app.store import Store


class FakeEvaluator:
    model_info = ModelInfo(mode="local", name="test-fixture-model")

    def __init__(self, fail_id=None):
        self.fail_id = fail_id

    async def evaluate(self, conversation, checklist):
        if conversation.conversation_id == self.fail_id:
            raise ProviderError("Fixture model unavailable")
        return [CriterionResult(criterion_id=c.criterion_id,
                                value=False if c.criterion_id == "critical" else True if c.answer_type == "boolean" else c.scale[-1],
                                rationale="Тестовый ответ", evidence_turns=[1]) for c in checklist.criteria]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(data_dir=self.root / "data", env_file=self.root / ".env", model_name="test-fixture-model")
        self.store = Store(self.settings.data_dir)
        self.service = EvaluationService(self.store, FakeEvaluator())
        self.source = self.root / "source.json"
        self.output = self.root / "results.json"
        records = [{"conversation_id": f"control-{i}", "channel": "agent chat", "language": "RU",
                    "transcript": [{"timestamp": "00:01", "speaker": "operator", "text": "Здравствуйте! Я Анна."}],
                    "audio_url": None} for i in range(10)]
        self.source.write_text(json.dumps({"isSuccess": True, "error": "", "data": {
            "dataset_group_id": "CONTROL", "total_records": 10, "records": records}}, ensure_ascii=False), encoding="utf-8")

    def command(self, args):
        with patch.object(cli, "runtime", return_value=(self.settings, self.store, self.service)), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return cli.main(args)

    def run_source(self):
        return self.command(["run", "--group-id", "CONTROL", "--subset", "prod", "--input", str(self.source), "--output", str(self.output)])

    def test_full_control_workflow_and_tamper_detection(self):
        self.assertEqual(self.run_source(), 0)
        payload = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["results"]), 10)
        self.assertEqual(payload["model"]["name"], "test-fixture-model")
        args = ["validate", "--group-id", "CONTROL", "--subset", "prod", "--file", str(self.output)]
        self.assertEqual(self.command(args), 0)
        payload["model"]["name"] = "changed-model"
        self.output.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(self.command(args), 1)

    def test_failed_dialogue_is_exported_with_error_and_nonzero_exit(self):
        self.service = EvaluationService(self.store, FakeEvaluator("control-4"))
        self.assertEqual(self.run_source(), 1)
        payload = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["results"]), 10)
        row = next(r for r in payload["results"] if r["conversation_id"] == "control-4")
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["criteria"], [])
        self.assertIsNone(row["score"])

    def test_wrong_source_group_is_not_imported(self):
        self.assertEqual(self.command(["fetch", "--group-id", "DEV", "--input", str(self.source)]), 1)
        self.assertEqual(self.store.list_datasets(), [])

    def test_nonfinite_json_does_not_replace_existing_export(self):
        self.output.write_text("existing", encoding="utf-8")
        with self.assertRaises(ValueError):
            cli.write_json(self.output, {"invalid": float("nan")})
        self.assertEqual(self.output.read_text(), "existing")


if __name__ == "__main__":
    unittest.main()
