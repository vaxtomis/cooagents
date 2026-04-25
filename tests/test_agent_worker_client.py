"""Phase 8b: ``CooagentsClient`` HTTP wiring + CAS header semantics.

Uses httpx's MockTransport so we never spin up a real server.
"""
from __future__ import annotations

import json

import httpx
import pytest

from src.agent_worker.cooagents_client import (
    CooagentsClient, CooagentsClientError,
)


def _make_client(handler) -> CooagentsClient:
    transport = httpx.MockTransport(handler)
    httpx_client = httpx.AsyncClient(
        base_url="http://control.test",
        headers={"X-Agent-Token": "tok"},
        transport=transport,
    )
    return CooagentsClient(
        base_url="http://control.test",
        agent_token="tok",
        client=httpx_client,
    )


async def test_get_files_index_success():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["agent_token"] = request.headers.get("X-Agent-Token")
        return httpx.Response(200, json={
            "workspace_id": "ws-1", "slug": "demo",
            "files": [{"relative_path": "a.md"}],
        })

    async with _make_client(handler) as c:
        body = await c.get_files_index("ws-1")
    assert captured["path"] == "/api/v1/workspaces/ws-1/files"
    assert captured["agent_token"] == "tok"
    assert body["slug"] == "demo"


async def test_post_file_sends_cas_none_for_first_write():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(201, json={"id": "wf-1"})

    async with _make_client(handler) as c:
        await c.post_file(
            "ws-1", relative_path="a.md", kind="other",
            payload=b"hi", expected_prior_hash=None,
        )
    assert captured["headers"]["x-expected-prior-hash"] == "none"


async def test_post_file_sends_hex_for_overwrite():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(201, json={"id": "wf-2"})

    async with _make_client(handler) as c:
        await c.post_file(
            "ws-1", relative_path="a.md", kind="other",
            payload=b"v2", expected_prior_hash="abc123",
        )
    assert captured["headers"]["x-expected-prior-hash"] == "abc123"


async def test_post_file_412_surfaces_etag_mismatch():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(412, json={
            "error": "etag_mismatch",
            "current_hash": "deadbeef",
            "expected_hash": None,
        })

    async with _make_client(handler) as c:
        with pytest.raises(CooagentsClientError) as exc:
            await c.post_file(
                "ws-1", relative_path="a.md", kind="other",
                payload=b"x", expected_prior_hash=None,
            )
    assert exc.value.status_code == 412
    assert exc.value.body["current_hash"] == "deadbeef"


async def test_post_file_rejects_non_str_non_none_cas():
    async def go():
        async with _make_client(lambda r: httpx.Response(201, json={})) as c:
            await c.post_file(
                "ws-1", relative_path="a.md", kind="other",
                payload=b"x", expected_prior_hash=42,  # type: ignore[arg-type]
            )

    with pytest.raises(ValueError):
        await go()
