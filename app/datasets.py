"""Read organizer datasets without changing source IDs, times or turn numbering."""
from __future__ import annotations

from urllib.parse import quote, unquote_plus, urlsplit, urlunsplit

import httpx

from app.domain import Conversation


class DatasetError(ValueError):
    pass


def parse_dataset(payload: dict) -> tuple[str, list[Conversation]]:
    try:
        if not isinstance(payload, dict) or payload.get("isSuccess") is not True:
            raise ValueError
        data = payload["data"]
        if not isinstance(data, dict):
            raise ValueError
        group = data["dataset_group_id"]
        total = data["total_records"]
        records = data["records"]
        if not isinstance(group, str) or not group.strip():
            raise ValueError
        if type(total) is not int or total < 1 or not isinstance(records, list) or total != len(records):
            raise ValueError
        conversations = [Conversation.model_validate(record) for record in records]
        ids = [item.conversation_id for item in conversations]
        if len(set(ids)) != len(ids):
            raise DatasetError("The dataset contains duplicate conversation IDs.")
        return group, conversations
    except DatasetError:
        raise
    except (KeyError, TypeError, ValueError):
        raise DatasetError("Invalid organizer dataset: check success, group, record count, IDs and transcript fields.") from None


def _dataset_url(url: str, group_id: str | None) -> str:
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme != "https" or not host.endswith(".api.powerplatform.com"):
            raise ValueError
        if parts.username or parts.password or parts.fragment or parts.port not in (None, 443):
            raise ValueError
        if group_id is None:
            return url
        if not isinstance(group_id, str) or not group_id.strip():
            raise ValueError
        # Signed parameters must keep their exact escaping and ordering.
        pieces = []
        replaced = False
        for raw in parts.query.split("&") if parts.query else []:
            if unquote_plus(raw.split("=", 1)[0]) == "group_id":
                if not replaced:
                    pieces.append("group_id=" + quote(group_id, safe=""))
                    replaced = True
            else:
                pieces.append(raw)
        if not replaced:
            pieces.append("group_id=" + quote(group_id, safe=""))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(pieces), ""))
    except (TypeError, ValueError):
        raise DatasetError("Use the organizer's HTTPS Power Platform dataset URL and a non-empty group ID.") from None


async def fetch_dataset(url: str, group_id: str | None = None) -> tuple[str, list[Conversation]]:
    target = _dataset_url(url, group_id)
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.get(target)
        if not response.is_success:
            raise DatasetError(f"The organizer API returned HTTP {response.status_code}.")
        group, conversations = parse_dataset(response.json())
        if group_id is not None and group != group_id:
            raise DatasetError("The organizer returned a different dataset group.")
        return group, conversations
    except DatasetError:
        raise
    except (httpx.HTTPError, ValueError):
        raise DatasetError("The organizer API could not be read or returned invalid JSON.") from None
