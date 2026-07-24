"""Trading-floor transport abstraction.

The vote logic never talks to Band directly — it talks to a RoomTransport.
BandTransport (client.py) is the real thing; InMemoryFloor is the drop-in
used by tests, the replay harness, and as the §9 fallback if the live API
fights us during the hack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class RoomMessage:
    sender: str
    text: str
    mentions: list[str] = field(default_factory=list)


class RoomTransport(Protocol):
    def post(self, sender: str, text: str, mentions: list[str] | None = None) -> None:
        """Post a message to the floor as ``sender`` (@mentioning ``mentions``)."""
        ...

    def history(self) -> list[RoomMessage]:
        """Full room transcript, oldest first."""
        ...


class InMemoryFloor:
    """Deterministic local floor — same interface, zero network."""

    def __init__(self) -> None:
        self._messages: list[RoomMessage] = []

    def post(self, sender: str, text: str, mentions: list[str] | None = None) -> None:
        self._messages.append(RoomMessage(sender=sender, text=text, mentions=mentions or []))

    def history(self) -> list[RoomMessage]:
        return list(self._messages)
