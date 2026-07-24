"""One-shot Band go-live: agents provisioned, room created, floor smoke-tested.

Run the moment we have the desk owner's Band account key:

    BAND_ADMIN_KEY=<key> python -m voting.band_setup

It will:
  1. create one Band identity per desk agent (news, realtime, historical,
     pm, evaluator) via the proxy registry — or reuse ones already registered
  2. create the trading-floor room as the PM
  3. post a smoke message from every agent (@mentioning the PM) and read the
     room back through the PM's scoped view
  4. print the exact env block to paste into backend/.env — after which the
     village/cycle floor runs through Band instead of in-memory

Idempotent: registered agents and their keys persist in voting/data/.
If Band's response fields differ from the docs (they weren't fully
specified), the error output shows the raw payload so the fix is a
one-line field rename in band_client.py / registry.py.
"""

from __future__ import annotations

import os
import sys

from .band_client import BandAgentClient
from .registry import AgentRegistry, RegistryError

DESK_AGENTS = ["news", "realtime", "historical", "pm", "evaluator"]


def main() -> None:
    admin_key = os.environ.get("BAND_ADMIN_KEY") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not admin_key:
        sys.exit("usage: BAND_ADMIN_KEY=<band account api key> python -m voting.band_setup")

    registry = AgentRegistry(admin_key=admin_key)

    # 1. One Band identity per desk agent.
    keys: dict[str, str] = {}
    for name in DESK_AGENTS:
        try:
            info = registry.register(name)
            keys[name] = registry.key_for(name)
            print(f"agent {name:<11} ready ({info['source']})")
        except RegistryError as e:
            sys.exit(f"FAILED registering {name}: {e}")

    clients = {n: BandAgentClient(n, k) for n, k in keys.items()}

    # 2. The PM opens the trading floor.
    try:
        chat_id = clients["pm"].create_chat()
        print(f"trading floor created: chat_id={chat_id}")
    except Exception as e:
        sys.exit(f"FAILED creating room: {e}")

    # 3. Smoke: everyone posts, PM reads the room back.
    for name, client in clients.items():
        if name == "pm":
            continue
        try:
            client.send_message(chat_id, f"🔧 {name} online.", mentions=["pm"])
            print(f"  {name} posted OK")
        except Exception as e:
            print(f"  {name} post FAILED: {e}")
    try:
        msgs = clients["pm"].list_messages(chat_id)
        print(f"PM reads {len(msgs)} messages in the room")
    except Exception as e:
        print(f"PM read FAILED: {e}")

    # 4. The env block that flips the floor from in-memory to Band.
    print("\n──── paste into backend/.env ────")
    print(f"BAND_CHAT_ID={chat_id}")
    print(f"BAND_ADMIN_KEY={admin_key}")
    for name, key in keys.items():
        print(f"BAND_KEY_{name.upper()}={key}")
    print("─────────────────────────────────")
    print("then restart the backend (or rerun the village/cycle) — the floor "
          "posts to Band; humans watching the room see every vote live.")


if __name__ == "__main__":
    main()
