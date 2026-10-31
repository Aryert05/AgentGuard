"""Deterministic local sample search. Results are explicitly not live web results."""
from __future__ import annotations

from pydantic import BaseModel


class SearchResult(BaseModel):
    title: str
    snippet: str
    source: str


class SearchArgs(BaseModel):
    query: str
    limit: int = 3


DATA = [
    SearchResult(title="Agent reliability", snippet="Reliability practices include observability, bounded retries, and clear failure states.", source="AgentGuard local sample"),
    SearchResult(title="LangGraph workflows", snippet="Stateful graphs connect nodes and route execution through explicit transitions.", source="AgentGuard local sample"),
    SearchResult(title="Tool calling", snippet="Tools let an agent invoke bounded functions and inspect structured results.", source="AgentGuard local sample"),
]


def search(query: str, limit: int = 3) -> dict[str, object]:
    if not query.strip():
        raise ValueError("Search query must not be empty")
    terms = set(query.lower().split())
    stop_words = {"a", "an", "and", "for", "find", "information", "look", "of", "search", "the", "to"}
    terms = {term for term in terms if term not in stop_words}
    ranked = sorted(DATA, key=lambda item: sum(t in (item.title + " " + item.snippet).lower() for t in terms), reverse=True)
    matched = [row for row in ranked if terms and any(t in (row.title + " " + row.snippet).lower() for t in terms)]
    return {"query": query, "source_type": "local_mock_dataset", "matched": bool(matched),
            "message": None if matched else "No matching results in the local mock dataset.",
            "results": [r.model_dump() for r in matched[:max(1, min(limit, 10))]]}
