"""End-to-end engine tests use controlled evaluator responses and temporary SQLite."""
import asyncio
from pathlib import Path
import tempfile
import unittest

from app.defaults import default_checklist
from app.domain import Checklist, Conversation, CriterionConfig, CriterionResult, ModelInfo, validate_and_score
from app.providers import ProviderError, ProviderNotConfigured
from app.service import EvaluationService
from app.store import ConflictError, Store


def conversation(index=0, channel="agent chat"):
    return Conversation(conversation_id=f"source-{index:02}", channel=channel, language="RU", transcript=[
        {"timestamp": "00:00", "speaker": "client", "text": "Как узнать тариф?"},
        {"timestamp": "00:04", "speaker": "operator", "text": "Здравствуйте, я Анна. Тариф доступен в приложении."},
        {"timestamp": "00:08", "speaker": "operator", "text": "Могу ещё помочь? До свидания."},
    ])


def criteria(checklist=None, *, contact=True, evidence=2):
    checklist = checklist or default_checklist()
    return [CriterionResult(
        criterion_id=c.criterion_id,
        value=False if c.criterion_id == "critical" else contact if c.criterion_id == "contact"
            else c.scale[-1] if c.answer_type == "scale" else True,
        rationale="Проверено по реплике оператора.", evidence_turns=[evidence],
    ) for c in checklist.criteria]


class FakeEvaluator:
    def __init__(self):
        self.model_info = ModelInfo(mode="local", name="test-controlled-evaluator")
        self.fail_ids = set()
        self.invalid_ids = set()
        self.calls = []

    async def evaluate(self, convo, checklist):
        self.calls.append(convo.conversation_id)
        if convo.conversation_id in self.fail_ids:
            raise ProviderError("Controlled test model error")
        return criteria(checklist, evidence=99 if convo.conversation_id in self.invalid_ids else 2)


class EngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name))
        self.evaluator = FakeEvaluator()
        self.service = EvaluationService(self.store, self.evaluator, concurrency=3)

    def load(self, count=1, group="TEST"):
        source = [conversation(i, channel="voice call" if group == "DEV" and count == 65 and i >= 60 else "agent chat")
                  for i in range(count)]
        self.store.import_dataset(group, source)
        return source

    async def test_restart_and_idempotent_reimport_preserve_assessment(self):
        source = self.load()
        assessed = await self.service.evaluate_one("TEST", source[0].conversation_id)
        reopened = Store(Path(self.directory.name))
        self.assertEqual(reopened.get_record("TEST", source[0].conversation_id), assessed)
        self.assertTrue(reopened.import_dataset("TEST", source)["already_imported"])
        changed = source[0].model_copy(deep=True)
        changed.transcript[0].text = "Другой запрос"
        with self.assertRaises(ConflictError):
            reopened.import_dataset("TEST", [changed])

    async def test_failed_chat_stays_in_control_export_and_can_retry(self):
        source = self.load(10, "CONTROL")
        self.evaluator.fail_ids.add(source[3].conversation_id)
        summary = await self.service.evaluate_dataset("CONTROL")
        self.assertEqual((summary["completed"], summary["failed"]), (9, 1))
        bundle = self.service.export("CONTROL", "prod", "team")
        self.assertEqual(len(bundle.results), 10)
        error = next(r for r in bundle.results if r.status == "error")
        self.assertEqual(error.conversation_id, source[3].conversation_id)
        self.assertEqual(error.criteria, [])
        self.assertIsNone(error.score)
        self.assertEqual(bundle.model.name, self.evaluator.model_info.name)
        self.evaluator.fail_ids.clear()
        self.evaluator.calls.clear()
        retried = await self.service.evaluate_dataset("CONTROL", retry_errors_only=True)
        self.assertEqual(retried["failed"], 0)
        self.assertEqual(self.evaluator.calls, [source[3].conversation_id])

    async def test_invalid_model_evidence_becomes_explicit_error(self):
        source = self.load()
        self.evaluator.invalid_ids.add(source[0].conversation_id)
        record = await self.service.evaluate_one("TEST", source[0].conversation_id)
        self.assertEqual(record["final_result"]["status"], "error")
        self.assertIsNone(record["final_result"]["score"])
        self.assertNotIn("99", record["final_result"]["error"])

    async def test_missing_model_fails_before_writing_results(self):
        source = self.load()

        class MissingModel:
            @property
            def model_info(self):
                raise ProviderNotConfigured("Configure model first")

        service = EvaluationService(self.store, MissingModel())
        with self.assertRaises(ProviderNotConfigured):
            await service.evaluate_dataset("TEST")
        self.assertIsNone(self.store.get_record("TEST", source[0].conversation_id)["final_result"])

    async def test_appeal_changes_final_score_and_retains_original(self):
        source = self.load()
        conv = source[0]
        original = validate_and_score(conv, default_checklist(), criteria(contact=False))
        self.store.save_result("TEST", conv.conversation_id, default_checklist(), self.evaluator.model_info, original)
        appeal = self.store.create_appeal("TEST", conv.conversation_id, "contact", "Представление есть в реплике 2")
        replacement = CriterionResult(criterion_id="contact", value=True,
                                      rationale="Оператор представился и поздоровался", evidence_turns=[2])
        resolved = self.service.decide(appeal["id"], "change", "Доказательство подтверждено", replacement)
        self.assertEqual(resolved["record"]["original_result"]["score"]["final_score"], 90)
        self.assertEqual(resolved["record"]["final_result"]["score"]["final_score"], 100)
        self.assertEqual(resolved["appeal"]["status"], "resolved")
        with self.assertRaises(ConflictError):
            self.service.decide(appeal["id"], "confirm", "Повтор")

    async def test_confirmation_and_stale_appeal_after_new_assessment(self):
        source = self.load()
        conv = source[0]
        await self.service.evaluate_one("TEST", conv.conversation_id)
        confirm = self.store.create_appeal("TEST", conv.conversation_id, "contact", "Проверьте")
        decided = self.service.decide(confirm["id"], "confirm", "Исходная оценка верна")
        self.assertEqual(decided["record"]["original_result"], decided["record"]["final_result"])
        stale = self.store.create_appeal("TEST", conv.conversation_id, "contact", "Проверьте ещё раз")
        await self.service.evaluate_one("TEST", conv.conversation_id)
        with self.assertRaises(ConflictError):
            self.service.decide(stale["id"], "confirm", "Старая оценка")

    async def test_checklist_edits_preserve_assessment_snapshot(self):
        source = self.load()
        assessed = await self.service.evaluate_one("TEST", source[0].conversation_id)
        current = self.store.get_checklist()
        current.criteria[0].description = "Новое пользовательское правило"
        updated = self.store.save_checklist(current)
        self.assertEqual(updated.version, 2)
        self.assertEqual(self.store.get_record("TEST", source[0].conversation_id)["checklist"], assessed["checklist"])
        with self.assertRaises(ConflictError):
            self.store.save_checklist(current)

    async def test_new_criterion_is_evaluated_from_configuration(self):
        source = self.load()
        custom = Checklist(id="custom", name="Пользовательский чек-лист", criteria=[
            CriterionConfig(criterion_id="next_step", label="Следующий шаг", description="Дал ли оператор следующий шаг?",
                            weight=100.0, answer_type="boolean", allow_na=False),
        ])
        self.store.save_checklist(custom, create=True)
        record = await self.service.evaluate_one("TEST", source[0].conversation_id, "custom")
        self.assertEqual([c["criterion_id"] for c in record["final_result"]["criteria"]], ["next_step"])
        self.assertEqual(record["final_result"]["score"]["final_score"], 100)

    async def test_official_dev_has_exact_65_and_export_never_omits_pending(self):
        source = self.load(65, "DEV")
        await self.service.evaluate_one("DEV", source[0].conversation_id)
        with self.assertRaises(ValueError):
            self.service.export("DEV", "dev", "team")
        await self.service.evaluate_dataset("DEV", retry_errors_only=True)
        bundle = self.service.export("DEV", "dev", "team")
        self.assertEqual(len(bundle.results), 65)
        with self.assertRaises(ValueError):
            self.service.export("DEV", "prod", "team")

    async def test_export_metadata_must_match_actual_models(self):
        source = self.load(10, "CONTROL")
        await self.service.evaluate_dataset("CONTROL")
        different = ModelInfo(mode="local", name="different-test-model")
        self.store.save_result("CONTROL", source[0].conversation_id, default_checklist(), different,
                               validate_and_score(source[0], default_checklist(), criteria()))
        with self.assertRaisesRegex(ValueError, "different models"):
            self.service.export("CONTROL", "prod", "team")

    async def test_overlapping_batch_starts_no_partial_work(self):
        source = self.load(3)
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        class BlockingEvaluator:
            model_info = ModelInfo(mode="local", name="blocking-test-model")

            async def evaluate(self, convo, checklist):
                calls.append(convo.conversation_id)
                entered.set()
                await release.wait()
                return criteria(checklist)

        service = EvaluationService(self.store, BlockingEvaluator())
        running = asyncio.create_task(service.evaluate_one("TEST", source[0].conversation_id))
        await entered.wait()
        try:
            with self.assertRaises(ConflictError):
                await service.evaluate_dataset("TEST")
            self.assertEqual(calls, [source[0].conversation_id])
            for convo in source:
                self.assertIsNone(self.store.get_record("TEST", convo.conversation_id)["final_result"])
        finally:
            release.set()
            await running
        self.assertIsNone(self.store.get_record("TEST", source[1].conversation_id)["final_result"])


if __name__ == "__main__":
    unittest.main()
