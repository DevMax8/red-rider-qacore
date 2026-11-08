"""Official exports validate the actual source channels, including error rows."""
from pathlib import Path
import tempfile
import unittest

from app.domain import Conversation, CriterionResult, ModelInfo
from app.providers import ProviderError
from app.service import EvaluationService
from app.store import Store


class ControlledEvaluator:
    model_info = ModelInfo(mode="local", name="source-shape-test-model")

    def __init__(self, fail_ids=()):
        self.fail_ids = set(fail_ids)

    async def evaluate(self, conversation, checklist):
        if conversation.conversation_id in self.fail_ids:
            raise ProviderError("Controlled source-shape test failure")
        return [CriterionResult(
            criterion_id=c.criterion_id,
            value=False if c.criterion_id == "critical" else True if c.answer_type == "boolean" else c.scale[-1],
            rationale="Controlled test result", evidence_turns=[1],
        ) for c in checklist.criteria]


def sources(chats, calls):
    records = []
    for index in range(chats + calls):
        is_call = index >= chats
        records.append(Conversation(
            conversation_id=f"original-{index:03d}",
            channel="voice call" if is_call else "agent chat",
            language="RU",
            transcript=[
                {"timestamp": "2026-11-08T09:00:00+00:00" if is_call else "00:00",
                 "speaker": "operator", "text": "Здравствуйте, я Анна. Чем могу помочь?"},
                {"timestamp": "2026-11-08T09:00:05+00:00" if is_call else "00:05",
                 "speaker": "client", "text": "Где посмотреть тариф?"},
            ],
            audio_url=None,
        ))
    return records


class SourceShapeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name))
        self.evaluator = ControlledEvaluator()
        self.service = EvaluationService(self.store, self.evaluator)

    async def prepare(self, chats, calls, group="SOURCE", fail_ids=()):
        records = sources(chats, calls)
        self.store.import_dataset(group, records)
        self.evaluator.fail_ids = set(fail_ids)
        await self.service.evaluate_dataset(group)
        return records

    async def test_dev_accepts_60_chats_and_5_calls_including_failed_call(self):
        records = await self.prepare(60, 5, fail_ids={"original-060"})
        bundle = self.service.export("SOURCE", "dev", "team")
        self.assertEqual(len(bundle.results), 65)
        self.assertEqual({r.conversation_id for r in bundle.results}, {c.conversation_id for c in records})
        error = next(r for r in bundle.results if r.conversation_id == "original-060")
        self.assertEqual(error.status, "error")
        self.assertEqual(error.criteria, [])
        self.assertIsNone(error.score)

    async def test_dev_rejects_all_chats_even_with_65_results(self):
        await self.prepare(65, 0)
        with self.assertRaisesRegex(ValueError, "60 agent chats and 5 voice calls"):
            self.service.export("SOURCE", "dev", "team")

    async def test_dev_rejects_incorrect_split_even_when_total_is_65(self):
        for chats, calls in ((61, 4), (59, 6)):
            with self.subTest(chats=chats, calls=calls):
                group = f"SOURCE-{chats}-{calls}"
                await self.prepare(chats, calls, group)
                with self.assertRaisesRegex(ValueError, "60 agent chats and 5 voice calls"):
                    self.service.export(group, "dev", "team")

    async def test_prod_accepts_exact_10_source_chats(self):
        await self.prepare(10, 0)
        self.assertEqual(len(self.service.export("SOURCE", "prod", "team").results), 10)

    async def test_prod_rejects_a_voice_call_even_with_10_results(self):
        await self.prepare(9, 1)
        with self.assertRaisesRegex(ValueError, "10 agent chats and no voice calls"):
            self.service.export("SOURCE", "prod", "team")

    async def test_subset_is_independent_of_organizer_group_id(self):
        await self.prepare(10, 0, group="DEV")
        self.assertEqual(len(self.service.export("DEV", "prod", "team").results), 10)

    async def test_unknown_subset_is_rejected(self):
        await self.prepare(10, 0)
        with self.assertRaisesRegex(ValueError, "subset must be dev or prod"):
            self.service.export("SOURCE", "other", "team")


if __name__ == "__main__":
    unittest.main()
