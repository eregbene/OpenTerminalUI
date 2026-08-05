from __future__ import annotations

from backend.research_agent.store import store


def remember(*, user_id: str, key: str, value: dict) -> dict:
    data = store.read()
    scoped = data.setdefault("memory", {}).setdefault(user_id, {})
    scoped[key] = value
    store.write(data)
    return value


def search(*, user_id: str, query: str) -> list[dict]:
    scoped = store.read().get("memory", {}).get(user_id, {})
    return [value for key, value in scoped.items() if query.lower() in key.lower() or query.lower() in str(value).lower()]
