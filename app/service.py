"""Assessment orchestration with checkpoints, deterministic reviews and export."""
from __future__ import annotations

import asyncio
from typing import Protocol

from app.domain import (
    Checklist, Conversation, CriterionResult, EvaluationResult, ModelInfo,
    build_export, validate_and_score,
)
from app.providers import ProviderError
from app.store import ConflictError, Store


class Evaluator(Protocol):
    @property
    def model_info(self) -> ModelInfo: ...
    async def evaluate(self, conversation: Conversation, checklist: Checklist) -> list[CriterionResult]: ...


class EvaluationService:
    def __init__(self, store: Store, evaluator: Evaluator, concurrency: int = 3):
        if not 1 <= concurrency <= 10:
            raise ValueError("Concurrency must be between 1 and 10")
        self.store = store
        self.evaluator = evaluator
        self.concurrency = concurrency
        self._inflight: set[tuple[str, str]] = set()
        self._semaphore = asyncio.Semaphore(concurrency)

    async def _evaluate(self, group_id: str, conversation_id: str, checklist: Checklist,
                        *, reserved: bool = False) -> dict:
        model = self.evaluator.model_info
        key = (group_id, conversation_id)
        if not reserved and key in self._inflight:
            raise ConflictError("This conversation is already being evaluated")
        self._inflight.add(key)
        try:
            async with self._semaphore:
                record = self.store.get_record(group_id, conversation_id)
                conversation = Conversation.model_validate(record["conversation"])
                try:
                    criteria = await self.evaluator.evaluate(conversation, checklist)
                    result = validate_and_score(conversation, checklist, criteria)
                except ProviderError as exc:
                    result = EvaluationResult(conversation_id=conversation_id, status="error", error=str(exc))
                except ValueError:
                    result = EvaluationResult(conversation_id=conversation_id, status="error",
                                              error="Model output failed checklist or evidence validation")
                self.store.save_result(group_id, conversation_id, checklist, model, result)
                return self.store.get_record(group_id, conversation_id)
        finally:
            self._inflight.discard(key)

    async def evaluate_one(self, group_id: str, conversation_id: str, checklist_id: str = "default") -> dict:
        return await self._evaluate(group_id, conversation_id, self.store.get_checklist(checklist_id))

    async def evaluate_dataset(self, group_id: str, checklist_id: str = "default",
                               retry_errors_only: bool = False) -> dict:
        self.evaluator.model_info
        checklist = self.store.get_checklist(checklist_id)
        conversations = self.store.list_conversations(group_id)
        selected = []
        for conversation in conversations:
            record = self.store.get_record(group_id, conversation.conversation_id)
            if not retry_errors_only or not record["final_result"] or record["final_result"]["status"] == "error":
                selected.append(conversation)
        keys = {(group_id, c.conversation_id) for c in selected}
        if keys.intersection(self._inflight):
            raise ConflictError("One or more conversations are already being evaluated")
        self._inflight.update(keys)
        try:
            outcomes = await asyncio.gather(
                *(self._evaluate(group_id, c.conversation_id, checklist, reserved=True) for c in selected),
                return_exceptions=True,
            )
        finally:
            self._inflight.difference_update(keys)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
        records = [self.store.get_record(group_id, c.conversation_id) for c in conversations]
        completed = sum(bool(r["final_result"] and r["final_result"]["status"] == "ok") for r in records)
        failed = sum(bool(r["final_result"] and r["final_result"]["status"] == "error") for r in records)
        return {"group_id": group_id, "total": len(conversations), "evaluated": len(selected),
                "completed": completed, "failed": failed}

    def decide(self, appeal_id: str, decision: str, reason: str,
               replacement: CriterionResult | None = None) -> dict:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("A review decision needs a reason")
        appeal = self.store.get_appeal(appeal_id)
        record = self.store.get_record(appeal["group_id"], appeal["conversation_id"])
        if not record["final_result"] or record["final_result"]["status"] != "ok":
            raise ConflictError("No successful result available for review")
        current = EvaluationResult.model_validate(record["final_result"])
        criteria = list(current.criteria)
        if decision == "change":
            if replacement is None or replacement.criterion_id != appeal["criterion_id"]:
                raise ValueError("A change needs a replacement for the appealed criterion")
            criteria = [replacement if c.criterion_id == replacement.criterion_id else c for c in criteria]
        elif decision != "confirm" or replacement is not None:
            raise ValueError("Use confirm without a replacement, or change with a replacement")
        result = validate_and_score(Conversation.model_validate(record["conversation"]),
                                    Checklist.model_validate(record["checklist"]), criteria)
        resolved = self.store.resolve_appeal(appeal_id, result, {
            "action": decision, "reason": reason,
            "replacement": replacement.model_dump(mode="json") if replacement else None,
        })
        return {"appeal": resolved,
                "record": self.store.get_record(appeal["group_id"], appeal["conversation_id"])}

    def export(self, group_id: str, subset: str, team_name: str):
        conversations = self.store.list_conversations(group_id)
        # Submission shape is checked against the preserved source records,
        # including conversations whose evaluation ended in an explicit error.
        chat_count = sum(c.channel == "agent chat" for c in conversations)
        call_count = sum(c.channel == "voice call" for c in conversations)
        if subset == "dev":
            if (len(conversations), chat_count, call_count) != (65, 60, 5):
                raise ValueError("DEV source must contain exactly 60 agent chats and 5 voice calls")
        elif subset == "prod":
            if (len(conversations), chat_count, call_count) != (10, 10, 0):
                raise ValueError("PROD source must contain exactly 10 agent chats and no voice calls")
        else:
            raise ValueError("subset must be dev or prod")
        records = [self.store.get_record(group_id, c.conversation_id) for c in conversations]
        if any(not r["final_result"] for r in records):
            raise ValueError("Evaluate every conversation before exporting; results cannot be omitted")
        model_data = records[0]["model"]
        checklist_data = records[0]["checklist"]
        if any(r["model"] != model_data for r in records):
            raise ValueError("Results use different models; re-evaluate the complete dataset")
        if any(r["checklist"] != checklist_data for r in records):
            raise ValueError("Results use different checklist versions; re-evaluate the complete dataset")
        checklist = Checklist.model_validate(checklist_data)
        results = []
        for conversation, record in zip(conversations, records):
            result = EvaluationResult.model_validate(record["final_result"])
            if result.status == "ok":
                recalculated = validate_and_score(conversation, checklist, result.criteria)
                if result != recalculated:
                    raise ValueError("Stored result has an inconsistent score")
            results.append(result)
        return build_export(team_name, ModelInfo.model_validate(model_data), results,
                            [c.conversation_id for c in conversations], subset, checklist)
