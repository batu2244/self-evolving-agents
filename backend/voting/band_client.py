"""Band REST transport (docs.band.ai, Agent API).

Deliberately thin (solution-design §3): agents post and poll over REST,
authenticated per-agent with X-API-Key. Each desk agent is a separate Band
identity, so the floor holds one client per analyst plus one for the PM.

Band routing rules that shape this client:
  - messages route ONLY to @mentioned agents; humans in the room see everything
  - agents only see messages that mention them
  - free tier retains data 24h — record the demo same-day

Endpoint shapes are from docs.band.ai/api/agent-api; verify field names
against the live API (or the OpenAPI spec at docs.band.ai) at the booth —
_pluck() below tolerates minor envelope differences.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .transport import RoomMessage

BAND_BASE_URL = os.environ.get("BAND_BASE_URL", "https://app.band.ai/api/v1")


def _pluck(data: Any, *keys: str) -> Any:
    """Tolerate {'data': {...}} / {'chats': [...]} style envelopes."""
    for key in keys:
        if isinstance(data, dict) and key in data:
            return data[key]
    return data


class BandAgentClient:
    """One Band identity (one API key) — an analyst, the PM, or the evaluator."""

    def __init__(self, name: str, api_key: str, base_url: str = BAND_BASE_URL) -> None:
        self.name = name
        self._http = httpx.Client(
            base_url=base_url,
            headers={"X-API-Key": api_key},
            timeout=15.0,
        )

    def create_chat(self, task_id: str | None = None) -> str:
        body = {"task_id": task_id} if task_id else {}
        r = self._http.post("/agent/chats", json=body)
        r.raise_for_status()
        chat = _pluck(r.json(), "data", "chat")
        return chat["id"]

    def send_message(self, chat_id: str, text: str, mentions: list[str]) -> None:
        # Band drops messages with no mentions — they route to no one.
        r = self._http.post(
            f"/agent/chats/{chat_id}/messages",
            json={"text": text, "mentions": [f"@{m.lstrip('@')}" for m in mentions]},
        )
        r.raise_for_status()

    def list_messages(self, chat_id: str) -> list[dict]:
        r = self._http.get(f"/agent/chats/{chat_id}/messages")
        r.raise_for_status()
        msgs = _pluck(r.json(), "data", "messages")
        return msgs if isinstance(msgs, list) else []

    def mark_processed(self, chat_id: str, message_id: str) -> None:
        self._http.post(f"/agent/chats/{chat_id}/messages/{message_id}/processed")


class BandFloor:
    """RoomTransport over one Band chat room, multiplexing per-agent clients.

    ``clients`` maps sender name -> that agent's BandAgentClient. ``reader``
    is whichever identity polls history (the PM — it is mentioned on every
    vote, so its scoped view contains the whole floor).
    """

    def __init__(self, chat_id: str, clients: dict[str, BandAgentClient], reader: str) -> None:
        self.chat_id = chat_id
        self._clients = clients
        self._reader = clients[reader]

    def post(self, sender: str, text: str, mentions: list[str] | None = None) -> None:
        self._clients[sender].send_message(self.chat_id, text, mentions or [])

    def history(self) -> list[RoomMessage]:
        out = []
        for m in self._reader.list_messages(self.chat_id):
            out.append(
                RoomMessage(
                    sender=str(_pluck(m.get("sender", {}), "name") or m.get("sender_id", "?")),
                    text=m.get("text", m.get("content", "")),
                    mentions=m.get("mentions", []),
                )
            )
        return out
