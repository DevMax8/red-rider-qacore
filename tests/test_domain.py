import unittest

from pydantic import ValidationError

from app.defaults import default_checklist
from app.domain import (
    Checklist, Conversation, CriterionConfig, CriterionResult, EvaluationResult,
    ModelInfo, Score, build_export, validate_and_score,
)


def conversation(identifier="demo-001"):
    return Conversation(conversation_id=identifier, channel="agent chat", language="RU",
                        transcript=[{"timestamp": f"00:0{i}", "speaker": "operator" if i % 2 else "client",
                                     "text": f"Реплика {i}"} for i in range(1, 9)])


def criteria(**overrides):
    values = {"contact": True, "discovery": None, "identification": None,
              "consultation": 15, "empathy": True, "dialogue": 7,
              "closure": True, "critical": False}
    values.update(overrides)
    return [CriterionResult(criterion_id=identifier, value=value,
                            rationale="Проверен соответствующий участок диалога.", evidence_turns=[1])
            for identifier, value in values.items()]


class DomainTests(unittest.TestCase):
    def test_pdf_example_score_and_critical_independence(self):
        result = validate_and_score(conversation(), default_checklist(), criteria())
        self.assertEqual(result.score.final_score, 85.38)
        self.assertFalse(result.score.critical_flag)
        critical = validate_and_score(conversation(), default_checklist(), criteria(critical=True))
        self.assertTrue(critical.score.critical_flag)
        self.assertEqual(critical.score.final_score, 85.38)

    def test_na_null_preserved_and_success_error_omitted(self):
        result = validate_and_score(conversation(), default_checklist(), criteria())
        data = result.model_dump(mode="json")
        self.assertIsNone(data["criteria"][1]["value"])
        self.assertNotIn("error", data)
        self.assertEqual(EvaluationResult.model_validate_json(result.model_dump_json()), result)

    def test_original_id_and_timestamp_preserved(self):
        item = conversation(" source-id ")
        item.transcript[0].timestamp = " 00:01 "
        self.assertEqual(item.conversation_id, " source-id ")
        self.assertEqual(item.transcript[0].timestamp, " 00:01 ")
        result = validate_and_score(item, default_checklist(), criteria())
        self.assertEqual(result.conversation_id, item.conversation_id)
        with self.assertRaises(ValidationError):
            conversation("  ")

    def test_wrong_types_na_and_scale_steps_rejected(self):
        for update in ({"contact": 1}, {"contact": None}, {"dialogue": True},
                       {"dialogue": 5}, {"consultation": 19}, {"critical": None}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_and_score(conversation(), default_checklist(), criteria(**update))
        for value in ("true", "7", float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                CriterionResult(criterion_id="contact", value=value, rationale="Причина", evidence_turns=[1])

    def test_evidence_must_be_nonempty_unique_integer_and_in_range(self):
        for evidence in ([], [0], [1, 1], [True], [1.5]):
            with self.subTest(evidence=evidence), self.assertRaises(ValidationError):
                CriterionResult(criterion_id="contact", value=True, rationale="Причина", evidence_turns=evidence)
        items = criteria()
        items[0].evidence_turns = [9]
        with self.assertRaises(ValueError):
            validate_and_score(conversation(), default_checklist(), items)

    def test_missing_duplicate_and_unknown_criteria_rejected(self):
        for items in (criteria()[:-1], criteria() + [criteria()[0]],
                      criteria()[:-1] + [CriterionResult(criterion_id="unknown", value=False,
                                                       rationale="Причина", evidence_turns=[1])]):
            with self.subTest(items=items), self.assertRaises(ValueError):
                validate_and_score(conversation(), default_checklist(), items)

    def test_custom_checklist_and_all_na_scoring(self):
        config = Checklist(id="custom", name="Тест", criteria=[
            CriterionConfig(criterion_id="custom_bool", label="Критерий", description="Тест",
                            weight=3, answer_type="boolean", allow_na=True),
            CriterionConfig(criterion_id="custom_scale", label="Шкала", description="Тест",
                            weight=1, answer_type="scale", allow_na=True, scale=[0, 2, 4]),
        ])
        items = [CriterionResult(criterion_id="custom_bool", value=True, rationale="Причина", evidence_turns=[1]),
                 CriterionResult(criterion_id="custom_scale", value=2, rationale="Причина", evidence_turns=[2])]
        result = validate_and_score(conversation(), config, items)
        self.assertEqual(result.score.final_score, 87.5)
        for item in items:
            item.value = None
        self.assertIsNone(validate_and_score(conversation(), config, items).score.final_score)

    def test_config_rejects_invalid_scales_duplicates_and_critical(self):
        for scale in ([0], [0, 0, 2], [1, 0, 2], [-1, 0, 2], [0, True]):
            with self.subTest(scale=scale), self.assertRaises(ValidationError):
                CriterionConfig(criterion_id="scale", label="Тест", description="Тест",
                                weight=1, answer_type="scale", scale=scale)
        with self.assertRaises(ValidationError):
            Checklist(id="x", name="x", criteria=[default_checklist().criteria[0]] * 2)
        with self.assertRaises(ValidationError):
            CriterionConfig(criterion_id="critical", label="Тест", description="Тест",
                            weight=1, answer_type="boolean")

    def test_export_includes_errors_and_source_order(self):
        ids = [f"control-{i}" for i in range(10)]
        results = [validate_and_score(conversation(identifier), default_checklist(), criteria()) for identifier in ids[:-1]]
        results.append(EvaluationResult(conversation_id=ids[-1], status="error", error="Модель недоступна"))
        bundle = build_export("red_rider", ModelInfo(mode="local", name="test-model"), results[::-1], ids, "prod")
        self.assertEqual([r.conversation_id for r in bundle.results], ids)
        error = bundle.model_dump(mode="json")["results"][-1]
        self.assertEqual(error["criteria"], [])
        self.assertIsNone(error["score"])
        self.assertEqual(error["error"], "Модель недоступна")

    def test_export_rejects_wrong_count_incomplete_duplicate_score_and_config(self):
        ids = [f"control-{i}" for i in range(10)]
        model = ModelInfo(mode="local", name="test-model")
        results = [validate_and_score(conversation(identifier), default_checklist(), criteria()) for identifier in ids]
        for candidate, expected, subset in ((results, ids, "dev"), (results[:-1], ids, "prod"),
                                             (results + [results[0]], ids, "prod"), (results, ids[:-1], "prod")):
            with self.subTest(subset=subset), self.assertRaises(ValueError):
                build_export("team", model, candidate, expected, subset)
        bad = results[0].model_copy(update={"score": Score(final_score=0, critical_flag=False)})
        with self.assertRaises(ValueError):
            build_export("team", model, [bad] + results[1:], ids, "prod")
        altered = default_checklist()
        altered.criteria[0].weight = 11
        with self.assertRaises(ValueError):
            build_export("team", model, results, ids, "prod", altered)

    def test_exact_65_dev_export(self):
        ids = [f"dev-{i}" for i in range(65)]
        results = [validate_and_score(conversation(identifier), default_checklist(), criteria()) for identifier in ids]
        exported = build_export("team", ModelInfo(mode="external_api", name="test-model"), results, ids, "dev")
        self.assertEqual(len(exported.results), 65)
        self.assertEqual(exported.schema_version, "1.0")

    def test_status_and_unknown_fields_rejected(self):
        for kwargs in ({"status": "error"}, {"status": "ok", "criteria": []},
                       {"status": "error", "error": "Причина", "score": {"final_score": 0, "critical_flag": False}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                EvaluationResult(conversation_id="id", **kwargs)
        with self.assertRaises(ValidationError):
            CriterionResult(criterion_id="contact", value=True, rationale="Причина", evidence_turns=[1], invented=1)


if __name__ == "__main__":
    unittest.main()
