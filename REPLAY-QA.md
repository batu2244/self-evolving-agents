# Replay QA log — DeltaDesk onboarding

How we used [Replay QA](https://qa.replay.io) to iterate on the onboarding
frontend, as evidence for the SwarmHack rubric ("well-designed SaaS apps with
QA completed and all discovered bugs fixed").

## Setup

- **Project:** `proj-deltadesk-onboarding-mrzflj6e` ("DeltaDesk Onboarding"),
  driven entirely through the REST API (`https://qa.replay.io/api/v1`) with a
  Bearer token — created with `target_url`, testing `instructions`, and a
  `design_document` describing expected behavior so the AI explorer knows
  correct from broken.
- **Exposure:** local dev stack (Vite :5173 proxying FastAPI :8000) published
  through a Cloudflare quick tunnel. We first tried ngrok, but its free-tier
  browser interstitial would have polluted the QA agent's view — trycloudflare
  serves the app clean.
- **Loop:** a background monitor polls `/projects/{id}/status` and
  `/projects/{id}/bugs` every 45 s → for each open bug we read the root-caused
  report (`GET /bugs/{id}`), fix it in the codebase, and `PATCH /bugs/{id}`
  to `fixed` with a fix description. Failed test runs with zero bugs get
  diagnosed by reading the journey definition (`GET /journeys/{id}`) and
  reproducing the flow locally.
- **Coverage Replay generated on its own:** 26 journeys / 27+ test runs,
  including full chat→ratify, conversational guardrails, keyboard-only
  operation, quick-reply-only flows, "just pick for me" defaults, crypto
  switch, post-proposal capital adjustment, and multi-slot free-text parsing.

## Fixes driven by Replay findings

| # | Source | Finding | Root cause | Fix | Files |
|---|--------|---------|------------|-----|-------|
| 1 | Bug `bug-mrzfpzf2-510a` (low, polish/glitches) | Header disclaimer "paper trading · not investment advice" fails WCAG AA contrast (3.21:1 < 4.5:1) | Design token `--color-faint: #5a635e` too dark on the ink background; same token used in footer, placeholders, labels | Raised tokens: `--color-faint → #7d8781` (5.37:1 on ink, ≥4.74:1 on all surfaces), `--color-muted → #98a19a` (7.5:1). One token change cleared every affected surface; ratios verified programmatically | `frontend/src/styles/global.css` |
| 2 | Failed run `run-mrzfmwct-op3j` ("edge cases and conversational guardrails", 0 bugs, died at 315 s) | Journey step "click Send with empty input, assert nothing sends" timed out | Send button was hard-`disabled` on empty input — automation clicks on a disabled element wait for actionability and time out | Send button stays clickable and no-ops on empty input (guard already in `send()`); `aria-disabled` + dimmed styling preserve the affordance | `frontend/src/modules/onboarding/components/Chat.tsx` |
| 3 | Local reproduction while diagnosing the guardrails journey | "I have 500 dollars" silently ignored (no minimum-bound pushback); "-5000" parsed as **$5,000** | Capital parser only knew `$`/`k`/`m`-style suffixes and ignored sign | Parser accepts `dollars/bucks/usd` suffixes and strips negative amounts before matching; regression tests added | `backend/app/onboarding/chat.py`, `test_onboarding.py` |
| 4 | Failed run `run-mrzfnsjs-3ve4` ("numbered quick replies and keyboard digits", 0 bugs) | Keyboard-driven journey could not complete | Digit hotkeys (1–3 answer the multiple choice) hijacked the first keystroke of numeric answers — typing "25000" as capital fired quick-reply #2 instead | Removed the digit-hotkey hijack; numbered chips remain click-to-answer | `frontend/src/modules/onboarding/components/Chat.tsx` |
| 5 | Local reproduction of post-proposal chips | Our own suggested chip "Double the capital" extracted nothing (turn was a no-op) | No support for relative capital adjustments in the extraction engine | Added relative-capital handling ("double/halve the capital") applied against the current envelope | `backend/app/onboarding/chat.py`, `router.py`, `test_onboarding.py` |
| 6 | Bug `bug-mrzg7973-npop` (medium) | "Send button permanently disabled — form submit handler missing" | Observed mid-fix: the run watched the app during the hot-reload window of fix #2 | Verified post-fix behavior headlessly (send works, empty input no-ops); marked fixed with explanation | — |

## Improvements shipped alongside (user-requested, pre-QA)

- Per-stock **indicative price, ▲/▼ 30-day change, and 30-day sparkline** in
  the proposal table (deterministic synthetic series so QA runs reproduce).
- Questions presented as **numbered multiple-choice** quick replies with a
  "Pick one — or type your own below" header.
- Per-stock **checkbox selection**; the ratified subset is forwarded to the
  agent committee as mandates (`selected` in the envelope API).

## Status

- 1 low bug filed & fixed (contrast), 1 medium bug root-caused to HMR-window
  observation, 3 journey failures diagnosed and hardened against.
- Loop continues until `open_bugs = 0` and test runs are green.
