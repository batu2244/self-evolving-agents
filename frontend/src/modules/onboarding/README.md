# Onboarding module

Conversational onboarding: a **desk concierge chat** that learns the risk
envelope (risk level, market, capital, target return) from free text — it
opens with popular stocks and infers your appetite from what you'd buy
(NVDA/TSLA reads aggressive US, KO reads conservative, BTC reads crypto).
When the envelope is complete it proposes a tracker + screened universe
in-chat; you **pick individual stocks**, and each selected stock is forwarded
to the trading committee as a mandate on ratify.

**This module is the surface Replay QA is pointed at** — every state
(thinking indicator, send failure + retry, slot tracker, empty selection,
bounds errors, replace-existing) is intentional and reachable.

## Boundary rules

- Everything onboarding lives here; nothing here imports from the rest of the
  app (no `@/pages`, no other modules).
- The app imports only from `@/modules/onboarding` (the `index.ts` barrel).
- Backend twin: `backend/app/onboarding/` — `chat.py` (deterministic engine),
  `llm.py` (optional Claude extraction, needs `ANTHROPIC_API_KEY`, falls back
  cleanly without it), `router.py`, `universe.py`. `types.ts` ↔ `schemas.py`
  + router chat models must change together.
- Committee handoff: backend `get_committee_mandates()` /
  `GET /api/onboarding/envelope` (`selected`); frontend `loadDesk().selected`.

## Public API

| Export | Purpose |
|---|---|
| `OnboardingChat` | The full chat flow; render it on a route, no props needed |
| `loadDesk()` / `clearDesk()` | Read/clear the ratified desk incl. `selected` stocks (localStorage; backend copy is authoritative) |
| types | `RiskEnvelope`, `UniverseProposal`, … |

## QA-relevant states

- Chat: thinking indicator (real backend latency), network/server error bubble
  with retry (re-sends the failed turn), quick-reply chips, disabled send on
  empty/pending input, auto-scroll
- Slot tracker fills as the concierge learns (— → value)
- Bounds handling in conversation: capital outside $1k–$10M and target outside
  0.25–25%/qtr are acknowledged and left unset
- Proposal card: per-stock checkboxes (all on by default), zero-selection
  disables ratify with inline error, ratify pending/error/success states
- Existing-desk banner when a constitution is already ratified
- Post-ratify: desk stays conversational ("switch to crypto" re-proposes)
