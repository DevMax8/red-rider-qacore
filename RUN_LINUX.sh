#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then python3 -m venv .venv; fi
if ! .venv/bin/python -c 'import fastapi,uvicorn,httpx,pydantic,dotenv' 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements.txt
fi
if [[ ! -e .env ]]; then cp .env.example .env; fi
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}"
