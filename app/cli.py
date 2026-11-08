"""Reproducible dataset evaluation and official JSON export from the terminal."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

from pydantic import ValidationError

from app.config import Settings
from app.datasets import DatasetError, fetch_dataset, parse_dataset
from app.domain import ExportBundle, build_export
from app.providers import OpenAICompatibleEvaluator, ProviderError
from app.service import EvaluationService
from app.store import Store


def runtime():
    settings = Settings.from_env()
    store = Store(settings.data_dir)
    evaluator = OpenAICompatibleEvaluator(base_url=settings.model_base_url, model=settings.model_name,
                                         api_key=settings.model_api_key, mode=settings.model_mode,
                                         timeout=settings.model_timeout, max_retries=settings.max_retries)
    return settings, store, EvaluationService(store, evaluator, settings.concurrency)


def read_json(path):
    def reject(value):
        raise ValueError("JSON contains a non-finite number")
    with Path(path).open(encoding="utf-8-sig") as source:
        return json.load(source, parse_constant=reject)


def write_json(path, payload):
    path = Path(path)
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False, allow_nan=False))


async def import_source(args, settings, store):
    if args.input:
        group_id, conversations = parse_dataset(read_json(args.input))
    else:
        if not settings.dataset_api_url:
            raise DatasetError("Set DATASET_API_URL or use --input with a local dataset JSON")
        group_id, conversations = await fetch_dataset(settings.dataset_api_url, args.group_id)
    if group_id != args.group_id:
        raise DatasetError("Source group ID differs from --group-id")
    if group_id == "DEMO":
        raise DatasetError("DEMO is reserved for the application's synthetic examples")
    store.import_dataset(group_id, conversations)
    emit({"group_id": group_id, "imported": len(conversations)})


def export(args, settings, service):
    if args.group_id == "DEMO":
        raise ValueError("Demonstration data cannot be submitted as official results")
    bundle = service.export(args.group_id, args.subset, settings.team_name)
    path = Path(args.output) if args.output else Path("results") / args.subset / "results.json"
    write_json(path, bundle.model_dump(mode="json"))
    emit({"exported": str(path), "records": len(bundle.results)})


def validate(args, store, service):
    bundle = ExportBundle.model_validate(read_json(args.file))
    expected_ids = [conversation.conversation_id for conversation in store.list_conversations(args.group_id)]
    checked = build_export(bundle.team_name, bundle.model, bundle.results, expected_ids, args.subset)
    expected = service.export(args.group_id, args.subset, bundle.team_name)
    left, right = checked.model_dump(mode="json"), expected.model_dump(mode="json")
    left["results"] = sorted(left["results"], key=lambda item: item["conversation_id"])
    right["results"] = sorted(right["results"], key=lambda item: item["conversation_id"])
    if left != right:
        raise ValueError("Export differs from persisted evaluations or their actual model; export again")
    emit({"valid": True, "records": len(bundle.results), "file": str(args.file)})


async def dispatch(args):
    settings, store, service = runtime()
    if args.command in {"fetch", "run"}:
        await import_source(args, settings, store)
    if args.command in {"evaluate", "run"}:
        summary = await service.evaluate_dataset(args.group_id, args.checklist_id,
                                                 getattr(args, "retry_errors_only", False))
        emit(summary)
        if args.command == "evaluate":
            return 1 if summary["failed"] else 0
    if args.command in {"export", "run"}:
        export(args, settings, service)
        if args.command == "run":
            return 1 if summary["failed"] else 0
    if args.command == "validate":
        validate(args, store, service)
    return 0


def parser():
    root = argparse.ArgumentParser(description="red_rider: fetch, evaluate, export and validate CQA results")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("fetch", "evaluate", "export", "validate", "run"):
        command = commands.add_parser(name)
        command.add_argument("--group-id", required=True)
        if name in {"fetch", "run"}:
            command.add_argument("--input", help="Local JSON in the original organizer API envelope")
        if name in {"evaluate", "run"}:
            command.add_argument("--checklist-id", default="default")
        if name == "evaluate":
            command.add_argument("--retry-errors-only", action="store_true")
        if name in {"export", "validate", "run"}:
            command.add_argument("--subset", required=True, choices=("dev", "prod"))
        if name in {"export", "run"}:
            command.add_argument("--output", help="Default results/<subset>/results.json")
        if name == "validate":
            command.add_argument("--file", required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return asyncio.run(dispatch(args))
    except KeyboardInterrupt:
        print("Interrupted. Completed assessments remain saved; use --retry-errors-only to resume.", file=sys.stderr)
        return 130
    except ValidationError:
        print("Error: invalid dataset or result fields. Check IDs, types, criteria and evidence.", file=sys.stderr)
    except (DatasetError, ProviderError, ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
    except OSError:
        print("Error: could not read or write the requested local file.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
