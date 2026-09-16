"""Minimal lens-api client (stdlib only, so the worker has no extra deps)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class LensApi:
    def __init__(self, base_url: str, token: str = "", timeout: float = 20.0) -> None:
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _req(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("accept", "application/json")
        if data is not None:
            req.add_header("content-type", "application/json")
        if self.token:
            req.add_header("authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 — operator-configured URL
            return json.loads(r.read().decode() or "{}")

    def health(self) -> dict[str, Any]:
        return self._req("GET", "/health")

    def seen(self, limit: int = 2000) -> list[dict[str, Any]]:
        return self._req("GET", f"/seen?limit={limit}").get("links", [])

    def mark_seen(self, links: list[dict[str, Any]]) -> dict[str, Any]:
        return self._req("POST", "/seen", {"links": links})

    def ingest(self, records: list[dict[str, Any]], source: str, version: str) -> dict[str, Any]:
        return self._req("POST", "/ingest", {"source": source, "version": version, "records": records})
