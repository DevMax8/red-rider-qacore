"""Local full-stack CQA application, with a documented API at /docs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field, StringConstraints

from app.config import PROJECT_ROOT, Settings
from app.datasets import DatasetError, fetch_dataset, parse_dataset
from app.demo import DEMO_GROUP, seed_demo
from app.domain import Checklist, ContractModel, CriterionResult
from app.providers import OpenAICompatibleEvaluator, ProviderError, ProviderNotConfigured
from app.service import EvaluationService
from app.store import ConflictError, Store

NonEmptyText = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class ImportRequest(ContractModel):
    payload: dict


class FetchRequest(ContractModel):
    group_id: str | None = None


class EvaluateRequest(ContractModel):
    group_id: str = Field(min_length=1)
    checklist_id: str = "default"


class BatchRequest(EvaluateRequest):
    retry_errors_only: bool = False


class AppealRequest(ContractModel):
    group_id: str = Field(min_length=1)
    criterion_id: str = Field(min_length=1)
    comment: NonEmptyText = Field(max_length=4000)


class DecisionRequest(ContractModel):
    decision: Literal["confirm", "change"]
    reason: NonEmptyText = Field(max_length=4000)
    replacement: CriterionResult | None = None


class SaveResultsRequest(ContractModel):
    group_id: str = Field(min_length=1)
    subset: Literal["dev", "prod"]
    team_name: NonEmptyText | None = None


class SettingsRequest(ContractModel):
    team_name: NonEmptyText | None = None
    model_name: str | None = None
    model_mode: Literal["local", "external_api"] | None = None
    model_base_url: str | None = None
    model_api_key: str | None = Field(default=None, max_length=8000)
    dataset_api_url: str | None = Field(default=None, max_length=16000)
    clear_api_key: bool = False
    clear_dataset_api_url: bool = False
    model_timeout: float | None = Field(default=None, ge=1, le=90)
    concurrency: int | None = Field(default=None, ge=1, le=10)


def make_evaluator(settings):
    return OpenAICompatibleEvaluator(base_url=settings.model_base_url,
                                     model=settings.model_name,
                                     api_key=settings.model_api_key,
                                     mode=settings.model_mode,
                                     timeout=settings.model_timeout,
                                     max_retries=settings.max_retries)


def create_app(settings: Settings | None = None, evaluator=None):
    settings = settings or Settings.from_env()
    store = Store(settings.data_dir)
    service = EvaluationService(store, evaluator or make_evaluator(settings), settings.concurrency)
    app = FastAPI(title="red_rider · Conversation Quality Analytics", version="1.0.0",
                  description="Оценка диалогов, доказательства, чек-листы, апелляции и экспорт результатов.")
    app.state.settings, app.state.store, app.state.service = settings, store, service
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                       allow_credentials=False, allow_methods=["GET", "POST", "PUT"],
                       allow_headers=["Content-Type"])

    @app.exception_handler(KeyError)
    async def not_found(request: Request, exc: KeyError):
        return JSONResponse(status_code=404, content={"detail": str(exc.args[0])})

    @app.exception_handler(ValueError)
    async def invalid_value(request: Request, exc: ValueError):
        return JSONResponse(status_code=409 if isinstance(exc, ConflictError) else 422,
                            content={"detail": str(exc)})

    @app.exception_handler(ProviderError)
    async def provider_error(request: Request, exc: ProviderError):
        return JSONResponse(status_code=503 if isinstance(exc, ProviderNotConfigured) else 502,
                            content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]})

    @app.get("/api/health")
    def health():
        current = app.state.settings
        return {"status": "ok", "model_configured": bool(current.model_name),
                "model_name": current.model_name or None, "model_mode": current.model_mode,
                "dataset_api_configured": bool(current.dataset_api_url)}

    @app.get("/api/settings")
    def get_settings():
        return app.state.settings.public()

    @app.put("/api/settings")
    def update_settings(body: SettingsRequest):
        if getattr(app.state.service, "_inflight", set()):
            raise ConflictError("Дождитесь завершения текущей оценки перед изменением настроек")
        updates = {key: value for key, value in body.model_dump(exclude_unset=True).items()
                   if key not in {"clear_api_key", "clear_dataset_api_url", "model_api_key", "dataset_api_url"} and value is not None}
        if body.clear_api_key:
            updates["model_api_key"] = ""
        elif body.model_api_key:
            updates["model_api_key"] = body.model_api_key
        if body.clear_dataset_api_url:
            updates["dataset_api_url"] = ""
        elif body.dataset_api_url:
            updates["dataset_api_url"] = body.dataset_api_url
        if "model_name" in updates:
            updates["model_name"] = updates["model_name"].strip()
        current = app.state.settings.updated(updates)
        # Validate and construct before saving; unconfigured model names are allowed.
        updated_service = EvaluationService(store, make_evaluator(current), current.concurrency)
        try:
            current.persist()
        except OSError:
            raise HTTPException(500, "Не удалось сохранить локальные настройки") from None
        app.state.settings, app.state.service = current, updated_service
        return current.public()

    @app.get("/api/checklists")
    def checklists():
        return store.list_checklists()

    @app.post("/api/checklists", status_code=201)
    def create_checklist(checklist: Checklist):
        return store.save_checklist(checklist, create=True)

    @app.get("/api/checklists/{checklist_id}")
    def get_checklist(checklist_id: str):
        return store.get_checklist(checklist_id)

    @app.put("/api/checklists/{checklist_id}")
    def update_checklist(checklist_id: str, checklist: Checklist):
        if checklist.id != checklist_id:
            raise HTTPException(422, "ID чек-листа в адресе и теле запроса должны совпадать")
        return store.save_checklist(checklist)

    @app.get("/api/datasets")
    def datasets():
        return store.list_datasets()

    @app.post("/api/datasets/import", status_code=201)
    def import_dataset(body: ImportRequest):
        group_id, conversations = parse_dataset(body.payload)
        if group_id == DEMO_GROUP:
            raise HTTPException(422, "Имя DEMO зарезервировано для демонстрационных примеров")
        return store.import_dataset(group_id, conversations)

    @app.post("/api/datasets/fetch", status_code=201)
    async def fetch(body: FetchRequest):
        current = app.state.settings
        if not current.dataset_api_url:
            raise HTTPException(503, "Добавьте ссылку API организаторов в настройках")
        try:
            group_id, conversations = await fetch_dataset(current.dataset_api_url, body.group_id)
        except DatasetError as exc:
            raise HTTPException(502, str(exc)) from None
        if body.group_id is not None and group_id != body.group_id:
            raise HTTPException(502, "Организатор вернул другой group_id")
        if group_id == DEMO_GROUP:
            raise HTTPException(422, "Имя DEMO зарезервировано для демонстрационных примеров")
        return store.import_dataset(group_id, conversations)

    @app.post("/api/demo", status_code=201)
    def demo():
        return seed_demo(store)

    @app.get("/api/conversations")
    def conversations(group_id: str = Query(min_length=1)):
        summaries = []
        for conversation in store.list_conversations(group_id):
            record = store.get_record(group_id, conversation.conversation_id)
            result = record["final_result"] or {}
            score = result.get("score") or {}
            summaries.append({"conversation_id": conversation.conversation_id,
                              "channel": conversation.channel, "language": conversation.language,
                              "turns": len(conversation.transcript), "status": result.get("status", "pending"),
                              "final_score": score.get("final_score"), "critical_flag": score.get("critical_flag", False),
                              "appeals_pending": sum(a.get("status") == "pending" for a in record["appeals"]),
                              "demo": group_id == DEMO_GROUP})
        return summaries

    @app.get("/api/conversations/{conversation_id}")
    def conversation(conversation_id: str, group_id: str = Query(min_length=1)):
        return store.get_record(group_id, conversation_id)

    @app.post("/api/conversations/{conversation_id}/evaluate")
    async def evaluate(conversation_id: str, body: EvaluateRequest):
        return await app.state.service.evaluate_one(body.group_id, conversation_id, body.checklist_id)

    @app.post("/api/evaluations")
    async def evaluate_batch(body: BatchRequest):
        return await app.state.service.evaluate_dataset(body.group_id, body.checklist_id, body.retry_errors_only)

    @app.post("/api/conversations/{conversation_id}/appeals", status_code=201)
    def appeal(conversation_id: str, body: AppealRequest):
        return store.create_appeal(body.group_id, conversation_id, body.criterion_id, body.comment)

    @app.get("/api/appeals")
    def appeals(group_id: str = Query(min_length=1)):
        return [appeal for conversation in store.list_conversations(group_id)
                for appeal in store.get_record(group_id, conversation.conversation_id)["appeals"]]

    @app.get("/api/appeals/{appeal_id}")
    def get_appeal(appeal_id: str):
        return store.get_appeal(appeal_id)

    @app.post("/api/appeals/{appeal_id}/decision")
    def decide(appeal_id: str, body: DecisionRequest):
        return app.state.service.decide(appeal_id, body.decision, body.reason, body.replacement)

    @app.post("/api/results/save")
    def save_results(body: SaveResultsRequest):
        if body.group_id == DEMO_GROUP:
            raise HTTPException(422, "Демонстрационные примеры нельзя сдавать как результаты DEV/PROD")
        current = app.state.settings
        bundle = app.state.service.export(body.group_id, body.subset, body.team_name or current.team_name)
        relative = Path("results") / body.subset / "results.json"
        # Fixed destinations only: the caller cannot select an arbitrary filesystem path.
        from app.cli import write_json
        try:
            write_json(current.env_file.parent / relative, bundle.model_dump(mode="json"))
        except OSError:
            raise HTTPException(500, "Не удалось сохранить results.json в папку проекта") from None
        return {"saved": True, "path": relative.as_posix(), "records": len(bundle.results)}

    @app.get("/api/results/export")
    def export(group_id: str, subset: Literal["dev", "prod"], team_name: str | None = None):
        if group_id == DEMO_GROUP:
            raise HTTPException(422, "Демонстрационные примеры нельзя сдавать как результаты DEV/PROD")
        bundle = app.state.service.export(group_id, subset, team_name or app.state.settings.team_name)
        text = json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        return Response(text, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="results.json"'})

    frontend = PROJECT_ROOT / "frontend"
    app.mount("/static", StaticFiles(directory=frontend, check_dir=False), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        path = frontend / "index.html"
        if not path.exists():
            return JSONResponse({"project": "red_rider", "docs": "/docs"})
        return FileResponse(path, headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
