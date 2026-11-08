"""Mocked adapter tests: never send transcripts or model credentials over a network."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.datasets import DatasetError, fetch_dataset, parse_dataset
from app.defaults import default_checklist
from app.domain import Conversation
from app.providers import OpenAICompatibleEvaluator, ProviderError, ProviderNotConfigured


def conversation_payload():
    return {"conversation_id": " id-exact ", "channel": "agent chat", "language": "RU",
            "transcript": [{"timestamp": " 00:01 ", "speaker": "client", "text": "Здравствуйте"},
                           {"timestamp": "00:02", "speaker": "operator", "text": "Здравствуйте, я Анна."}],
            "audio_url": None}


def dataset_payload():
    return {"isSuccess": True, "error": "", "data": {"dataset_group_id": "DEV", "total_records": 1,
                                                       "records": [conversation_payload()]}}


def response_criteria():
    return [{"criterion_id": c.criterion_id,
             "value": False if c.criterion_id == "critical" else (c.scale[-1] if c.answer_type == "scale" else True),
             "rationale": "Проверено по реплике", "evidence_turns": [2]}
            for c in default_checklist().criteria]


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_config_is_checked_before_model_info(self):
        for kwargs in ({"model": ""}, {"base_url": "bad-url"}, {"base_url": "https://user:secret@model.example/v1"},
                       {"timeout": 91}, {"max_retries": 3}, {"mode": "invented"}):
            with self.subTest(kwargs=kwargs):
                config = {"base_url": "https://model.example/v1", "model": "actual-model", **kwargs}
                with self.assertRaises(ProviderNotConfigured):
                    OpenAICompatibleEvaluator(**config).model_info

    async def test_request_uses_actual_model_and_original_turn_numbers(self):
        captured = []

        def respond(request):
            captured.append(request)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"criteria": response_criteria()})}}]})

        original_client = httpx.AsyncClient
        transport = httpx.MockTransport(respond)
        with patch("app.providers.httpx.AsyncClient", side_effect=lambda **kw: original_client(transport=transport, **kw)):
            evaluator = OpenAICompatibleEvaluator("https://model.example/v1", "actual-model", api_key="private-key")
            criteria = await evaluator.evaluate(Conversation.model_validate(conversation_payload()), default_checklist())
        self.assertEqual(len(criteria), 8)
        request = captured[0]
        payload = json.loads(request.content)
        supplied = json.loads(payload["messages"][1]["content"])
        self.assertEqual(str(request.url), "https://model.example/v1/chat/completions")
        self.assertEqual(payload["model"], "actual-model")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual([turn["turn"] for turn in supplied["transcript"]], [1, 2])
        self.assertEqual(supplied["transcript"][0]["timestamp"], " 00:01 ")
        self.assertEqual(supplied["conversation_id"], " id-exact ")
        self.assertNotIn("private-key", repr(evaluator))

    async def test_rate_limit_retries_and_errors_are_safe(self):
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(429, text="sensitive upstream detail")

        original_client = httpx.AsyncClient
        with patch("app.providers.httpx.AsyncClient", side_effect=lambda **kw: original_client(transport=httpx.MockTransport(respond), **kw)), \
                patch("app.providers.asyncio.sleep", new_callable=AsyncMock) as sleep:
            evaluator = OpenAICompatibleEvaluator("https://model.example/v1", "actual-model", api_key="private-key")
            with self.assertRaises(ProviderError) as failure:
                await evaluator.evaluate(Conversation.model_validate(conversation_payload()), default_checklist())
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.await_count, 2)
        self.assertNotIn("private-key", str(failure.exception))
        self.assertNotIn("sensitive upstream", str(failure.exception))

    async def test_invalid_json_and_truncated_response_are_errors(self):
        invalid_bodies = [
            {"choices": [{"message": {"content": "not-json"}}]},
            {"choices": [{"message": {"content": '{"criteria":[],"score":100}'}}]},
            {"choices": [{"finish_reason": "length", "message": {"content": '{"criteria":[]}'}}]},
            {"choices": [{"message": {"refusal": "refused", "content": ""}}]},
        ]
        for body in invalid_bodies:
            with self.subTest(body=body), self.assertRaises(ProviderError):
                OpenAICompatibleEvaluator._parse_response(httpx.Response(200, json=body))

    async def test_cancellation_propagates(self):
        def respond(request):
            raise asyncio.CancelledError

        original_client = httpx.AsyncClient
        with patch("app.providers.httpx.AsyncClient", side_effect=lambda **kw: original_client(transport=httpx.MockTransport(respond), **kw)):
            evaluator = OpenAICompatibleEvaluator("https://model.example/v1", "actual-model")
            with self.assertRaises(asyncio.CancelledError):
                await evaluator.evaluate(Conversation.model_validate(conversation_payload()), default_checklist())


class DatasetTests(unittest.IsolatedAsyncioTestCase):
    async def test_parse_preserves_source(self):
        group, records = parse_dataset(dataset_payload())
        self.assertEqual(group, "DEV")
        self.assertEqual(records[0].conversation_id, " id-exact ")
        self.assertEqual(records[0].transcript[0].timestamp, " 00:01 ")
        self.assertEqual([t.speaker for t in records[0].transcript], ["client", "operator"])

    async def test_count_duplicates_and_invalid_roles_are_rejected(self):
        malformed = []
        payload = dataset_payload()
        payload["data"]["total_records"] = 2
        malformed.append(payload)
        payload = dataset_payload()
        payload["data"]["records"] *= 2
        payload["data"]["total_records"] = 2
        malformed.append(payload)
        payload = dataset_payload()
        payload["data"]["records"][0]["transcript"][0]["speaker"] = "invented"
        malformed.append(payload)
        malformed.extend([{}, {"isSuccess": 1}, {"isSuccess": False}])
        for payload in malformed:
            with self.subTest(payload=payload), self.assertRaises(DatasetError):
                parse_dataset(payload)

    async def test_signed_query_bytes_are_preserved(self):
        captured = []
        payload = dataset_payload()
        payload["data"]["dataset_group_id"] = "CONTROL / 1"

        def respond(request):
            captured.append(request)
            return httpx.Response(200, json=payload)

        original_client = httpx.AsyncClient
        url = "https://environment.api.powerplatform.com/invoke?sp=%2Fmanual%2Frun&sig=a%2fb+Z%3D&group_id=DEV&sv=1.0"
        with patch("app.datasets.httpx.AsyncClient", side_effect=lambda **kw: original_client(transport=httpx.MockTransport(respond), **kw)):
            group, _ = await fetch_dataset(url, "CONTROL / 1")
        self.assertEqual(group, "CONTROL / 1")
        self.assertEqual(captured[0].url.query, b"sp=%2Fmanual%2Frun&sig=a%2fb+Z%3D&group_id=CONTROL%20%2F%201&sv=1.0")

    async def test_unsafe_hosts_and_redirects_are_not_followed(self):
        for url in ("http://environment.api.powerplatform.com/invoke", "https://127.0.0.1/x",
                    "https://environment.api.powerplatform.com.evil.example/x", "https://user:secret@x.api.powerplatform.com/x"):
            with self.subTest(url=url), self.assertRaises(DatasetError):
                await fetch_dataset(url)
        original_client = httpx.AsyncClient
        calls = []

        def redirect(request):
            calls.append(request)
            return httpx.Response(302, headers={"Location": "https://evil.example"})

        with patch("app.datasets.httpx.AsyncClient", side_effect=lambda **kw: original_client(transport=httpx.MockTransport(redirect), **kw)):
            with self.assertRaises(DatasetError) as failure:
                await fetch_dataset("https://environment.api.powerplatform.com/x?sig=private-sig")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("private-sig", str(failure.exception))

    async def test_requested_group_must_match_response(self):
        original_client = httpx.AsyncClient
        with patch("app.datasets.httpx.AsyncClient", side_effect=lambda **kw: original_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=dataset_payload())), **kw)):
            with self.assertRaises(DatasetError):
                await fetch_dataset("https://environment.api.powerplatform.com/x", "CONTROL")


if __name__ == "__main__":
    unittest.main()
