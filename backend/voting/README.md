# `voting/` — the Band trading floor (§3 of solution-design.md)

**Self-contained module — owned by the voting workstream.** Other agents:
don't edit files here; integrate via `voting.api.router` or
`voting.floor.run_vote_cycle`. Nothing in here reaches outside the module
except reading `BAND_*` env vars.

Two voting modes, both on the Band room record:

1. **Deliberation (primary).** Each agent's entry ticket is *the position
   change it wants* — nothing more. Then: one case each → one rebuttal per
   conflicting pair → the evaluation agent scores every argument (Claude
   judge, heuristic fallback) and blends the proposed changes weighted by
   `argument_score × track_record_credibility`. Agents execute every cycle;
   agents that performed poorly recently carry less credibility (floor 0.1).
2. **Weighted tally (fallback).** The simpler §3 vote: structured votes,
   weighted majority, challenge round, decision memo.

External services join through the **agent proxy**: register a Band agent
(bring a key, or the proxy creates one via `BAND_ADMIN_KEY` on Band's
`POST /me/agents/register`) and fetch its key back from the API.

## Layout

```
voting/
  deliberation.py position change → case → rebuttal → judged verdict
  judge.py        ClaudeJudge (opus-4-8, structured output) + HeuristicJudge
  track_record.py per-agent credibility from execution history (EW, floor 0.1)
  registry.py     agent proxy: register/create Band agents, serve their keys
  types.py        uniform Vote/Rebuttal/Challenge/DecisionMemo schema (§3)
  tally.py        pure weighted-tally rules (fallback mode)
  messages.py     Band message format: human-readable header + JSON fence
  floor.py        run_vote_cycle(): votes → challenge round → re-tally → memo
  transport.py    RoomTransport protocol + InMemoryFloor (tests/replay/fallback)
  band_client.py  Band REST client (app.band.ai/api/v1, X-API-Key per agent)
  api.py          FastAPI APIRouter (mount it) + create_app() for standalone dev
  demo.py         scripted deliberation cycle — prints the floor transcript
  data/           local state: registered agent keys, track record (gitignored)
  tests/          deliberation + tally + floor tests
```

## Run (from `backend/`)

```bash
.venv/bin/python -m pytest voting/tests -q          # tests
.venv/bin/python -m voting.demo                     # scripted binary-vote demo
.venv/bin/python -m voting.simulate NVDA            # one-off vote on live data
.venv/bin/python -m voting.cycle NVDA               # ← run this every 10 min
.venv/bin/uvicorn 'voting.api:create_app' --factory --reload   # standalone API
```

## The 10-minute loop

Decisions are made every 10 minutes, not daily. `voting.cycle` is the loop
body — invoke it on a 10-minute schedule (cron / `/loop 10m` / Guild
trigger). Each run first **grades the previous cycle** (realized move since
the last verdict scores every agent's stance: right side of a ±0.2% move =
full ±1, credibility updates with alpha=0.1 damping), then votes on fresh
5-minute intraday data (last-10-min tape, hour trend vs VWAP, news
backdrop; the GOOGL news analyst joins for GOOGL). State per symbol lives
in `voting/data/cycle_state_<symbol>.json`. Use `BTC-USD` outside US
market hours — it trades 24/7.

## Integrating

Main backend app mounts the router (all routes live under `/api/voting/*`):

```python
from voting.api import router as voting_router
app.include_router(voting_router)
```

Guild agents (or the replay harness) call the floor directly:

```python
from voting.floor import run_vote_cycle
memo = run_vote_cycle(cycle_id, votes_by_analyst, analysts, weights, room)
```

## API

Agent proxy (external services join the desk):
- `POST /api/voting/agents` — `{name, band_key?}`. With `band_key`, registers
  an existing Band identity; without, creates the agent on Band via
  `BAND_ADMIN_KEY` and captures its one-time key.
- `GET /api/voting/agents` · `GET /api/voting/agents/{name}/key` — the proxy
  returns the Band key for a particular agent. ⚠️ No auth — localhost/team
  network only.

Deliberation (staged; external agents drive it over HTTP):
- `POST /api/voting/deliberations` — open a session.
- `POST /api/voting/deliberations/{id}/position` — the agent's entry point:
  `{agent, ticker, current, target}` — the change it wants, nothing more.
- `POST /api/voting/deliberations/{id}/case` — argue for it (first case
  closes the position window).
- `POST /api/voting/deliberations/{id}/rebuttal` — answer a conflicting agent.
- `POST /api/voting/deliberations/{id}/verdict` — judge scores × credibility
  → blended final positions (idempotent).
- `GET /api/voting/deliberations/{id}` — session state.

Track record:
- `POST /api/voting/outcomes` — `{agent, score∈[-1,1]}` after each outcome
  window; poor scores lower the agent's future influence (floor 0.1).
- `GET /api/voting/credibility` — current credibility snapshot.

Dashboard & fallback:
- `GET /api/voting/floor` — full room transcript (floor feed UI).
- `POST /api/voting/cycle` · `GET /api/voting/memo` — the simple
  weighted-tally mode (§3 fallback).

Judge: set `ANTHROPIC_API_KEY` and verdicts use Claude (`claude-opus-4-8`,
structured output); without it a deterministic heuristic judge runs, so the
pipeline never blocks on an LLM.

## Going live on Band

The floor is transport-agnostic. Without env vars it runs on `InMemoryFloor`
(deterministic — used by tests and the 10-day replay). To run through a real
Band room, set:

```
BAND_CHAT_ID=<room id>
BAND_KEY_SENTIMENT=… BAND_KEY_REALTIME=… BAND_KEY_HISTORICAL=…
BAND_KEY_PM=… BAND_KEY_EVALUATOR=…
```

Each desk agent is its own Band identity/key (in production these are
runtime-injected via the custom Guild integration, per §8). Band routing to
remember: messages only reach @mentioned agents, humans see everything, free
tier retains 24h — record the demo same-day. Endpoint field names in
`band_client.py` follow docs.band.ai but should be verified against the live
API at the booth; `_pluck()` tolerates minor envelope differences.

## Tally rules (concrete)

Per ticker: conviction mass per direction = Σ weightₐ × confidenceₐ. The top
buy/sell direction must clear `majority_threshold` (default 0.5) of total
mass or the desk holds. Unanimous → full size; majority → `split_size_factor`
haircut (default 0.5); winners' own size-classes cap the position either way
(full=1.0, half=0.5, probe=0.25). A losing-side analyst with confidence ≥
`challenge_threshold` (default 0.7) triggers one challenge round: it
@mentions the majority, each challenged analyst posts one rebuttal
(optionally revising confidence), and the PM re-tallies — so a confident
dissenter really can flip a knife-edge decision, on the record.
