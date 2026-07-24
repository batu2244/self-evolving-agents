"""Agent registry + Band key proxy.

External services join the floor through this: they either bring their own
Band agent (register name + existing key) or ask the proxy to create one —
in which case we call Band's Human API (`POST /api/v1/me/agents/register`,
authenticated with the desk owner's BAND_ADMIN_KEY) and capture the
one-time API key Band returns.

The proxy then serves each agent's key back on request so the service can
authenticate to Band directly. Keys persist in a local JSON file (gitignored).

⚠️ Hackathon trust model: any caller who can reach this API can read agent
keys. Keep it bound to localhost / the team network; do not deploy public.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

BAND_BASE_URL = os.environ.get("BAND_BASE_URL", "https://app.band.ai/api/v1")
DEFAULT_STORE = Path(__file__).parent / "data" / "agents.json"


class RegistryError(Exception):
    pass


def _pluck(data: Any, *keys: str) -> Any:
    for key in keys:
        if isinstance(data, dict) and key in data:
            return data[key]
    return data


def _extract_key(payload: Any) -> str | None:
    """Band returns the one-time key under a name we can't fully rely on —
    accept the obvious candidates."""
    obj = _pluck(payload, "data", "agent")
    if not isinstance(obj, dict):
        return None
    for k in ("api_key", "apiKey", "key", "token"):
        if isinstance(obj.get(k), str):
            return obj[k]
    return None


class AgentRegistry:
    def __init__(
        self,
        path: Path | str | None = None,
        admin_key: str | None = None,
        base_url: str = BAND_BASE_URL,
    ) -> None:
        self._path = Path(path) if path else DEFAULT_STORE
        self._admin_key = admin_key or os.environ.get("BAND_ADMIN_KEY")
        self._base_url = base_url
        self._agents: dict[str, dict] = {}
        self._load()

    # -- provisioning ------------------------------------------------------

    def register(self, name: str, band_key: str | None = None) -> dict:
        """Register an agent. With ``band_key``: store as-is (external service
        brings its own Band identity). Without: create the agent on Band via
        the admin key and capture the returned one-time key."""
        if name in self._agents:
            return self._public(name)

        if band_key:
            self._agents[name] = {"name": name, "band_key": band_key, "source": "external"}
        else:
            key, agent_id = self._create_on_band(name)
            self._agents[name] = {
                "name": name, "band_key": key, "band_agent_id": agent_id, "source": "created",
            }
        self._save()
        return self._public(name)

    def _create_on_band(self, name: str) -> tuple[str, str | None]:
        if not self._admin_key:
            raise RegistryError(
                "no BAND_ADMIN_KEY configured — either provide band_key when "
                "registering, or set the desk owner's Band account key"
            )
        r = httpx.post(
            f"{self._base_url}/me/agents/register",
            headers={"X-API-Key": self._admin_key},
            json={"name": name},
            timeout=15.0,
        )
        r.raise_for_status()
        payload = r.json()
        key = _extract_key(payload)
        if not key:
            raise RegistryError(f"Band response had no recognizable api key: {payload}")
        obj = _pluck(payload, "data", "agent")
        agent_id = obj.get("id") if isinstance(obj, dict) else None
        return key, agent_id

    # -- lookup ------------------------------------------------------------

    def key_for(self, name: str) -> str:
        try:
            return self._agents[name]["band_key"]
        except KeyError:
            raise RegistryError(f"unknown agent: {name}") from None

    def list_agents(self) -> list[dict]:
        return [self._public(n) for n in sorted(self._agents)]

    def _public(self, name: str) -> dict:
        a = self._agents[name]
        return {"name": a["name"], "source": a["source"], "band_agent_id": a.get("band_agent_id")}

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        if self._path.exists():
            self._agents = json.loads(self._path.read_text())

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._agents, indent=2))
