# DeltaDesk — Design Document

*A self-improving multi-agent trading desk. SwarmHack (Self-Evolving Agents Hackathon), SF, July 24 2026. Stack anchored on **Guild.ai** (control plane) + **Replay QA** (front-end quality), optional **Pioneer** (sentiment models). Paper trading only — this is a demo of a learning system, not investment advice.*

---

## 1. Concept

DeltaDesk is a trading desk staffed by agents and graded by its own results. The user states four things: **risk level, target return, capital committed, and market/currency** (US equities, EU equities, or crypto). From that envelope the desk selects a **tracker** (benchmark ETF/index) and a small stock universe, then spawns a committee of specialist agents — a **semantic sentiment agent**, a **real-time data agent**, and a **historical performance agent** — that trade **once per day at a fixed time**, autonomously, on a Guild time trigger.

After every trade the desk computes its **decision delta** — did the decision beat what doing nothing (holding the tracker) would have earned? A negative delta forces the responsible agents to refine; a strongly positive delta reinforces the current configuration. The desk's edge is not any single signal — it's that **the committee's composition, weights, and playbooks are themselves the thing being traded and improved every day.**

## 2. Onboarding → the risk envelope

A four-question wizard (this is the front-end Replay will QA):

| Input | Example | What it constrains |
|---|---|---|
| Risk level | conservative / balanced / aggressive | max position size, max daily drawdown, volatility ceiling of eligible stocks, stop rules |
| Target return | "beat the tracker by 2%/quarter" | how aggressive signals must be before the PM acts; defines "success" for the delta |
| Capital committed | $10,000 (paper) | position sizing, max concurrent positions |
| Market / currency | US equities (USD) / EU (EUR) / crypto (24/7) | tradable universe, trading window, benchmark tracker |

The **universe-selector agent** turns the envelope into: one tracker (e.g., SPY for balanced-US, QQQ for aggressive-US, BTC for crypto) and 5–10 stocks screened from the tracker's constituents by the risk rules (volatility band, liquidity, sector spread). The envelope and universe live in **Guild workspace context** — the desk's constitution, which the improvement loops may amend only *within* the user's stated risk bounds, never beyond them.

## 3. The agent committee

All agents run on Guild (TypeScript `llmAgent`s, credentials injected, sessions audited). Each analyst emits the same structured output: `{signal: -1..+1 per ticker, confidence, rationale}` — uniform outputs are what make attribution (§5) possible.

- **Sentiment agent** — reads the last 24h of headlines for the universe (Alpaca News API / Finnhub free tier); classifies direction and strength per ticker. *Optional Pioneer grounding:* classification via GLiNER2 (schema call, no prompt engineering) instead of raw LLM votes — and sentiment classification is exactly the task their fine-tuning loop improves later (§6, Loop 3).
- **Real-time data agent** — snapshot of current prices vs open, gap analysis, volume anomalies, tracker momentum (Alpaca Market Data API, free tier).
- **Historical performance agent** — pattern read on the past 30–90 days: trend, mean-reversion setups, how each ticker historically behaves on days like today.
- **Portfolio manager (PM) agent** — the only agent that acts. Combines the three signals with the **current learned weights** `w = (w_sent, w_rt, w_hist)`, applies risk rules from the envelope, decides buy/sell/hold and sizes, and executes via the Alpaca **paper** trading API. Also writes the day's *decision memo*: what it did, why, and what each analyst claimed — the prediction that tomorrow's delta will grade.
- **Evaluator agent** — runs after the outcome window; computes the delta, attributes it, and applies the improvement loops (§6). It is the only agent with write access to weights and playbooks.

### The vote — decision by committee, on the record (Band)

The committee does not hand signals to the PM privately; it **votes on a trading floor — a Band chat room** — and the room is the desk's decision record.

Each cycle, every analyst posts a structured vote message to the room: `VOTE {ticker, direction, size-class, confidence, one-line rationale}`. The PM tallies a **weighted vote** using the current learned weights (Loop 1): a weighted majority is required to trade at all; unanimity earns full position size; a split vote cuts size or forces a hold. One rule makes it a deliberation rather than a poll: a dissenting analyst with confidence above threshold triggers a **challenge round** — it @mentions the majority, posts its objection, the challenged agents each post one rebuttal, and the PM re-tallies. Then the PM posts the final decision memo to the room and executes.

The room closes the learning loop in public: next cycle, the evaluator posts yesterday's grade *into the same room* — each analyst's score against the realized outcome, and, on a losing day, the playbook amendment issued to the worst analyst. The desk is coached where it argued.

Why this is the right architecture and not sponsor garnish: the vote gives the delta system its **paper trail** (every decision is reconstructible: who voted what, who dissented, who was right), gives the human real oversight (the user and the judges can watch the floor live — humans see everything in a Band room, agents see their @mentions), and gives the demo its best 30 seconds — agents visibly arguing about a trade, live. Integration is deliberately thin: analysts post and poll via Band's REST API (`app.band.ai/api/v1`, `X-API-Key`), wrapped as a **custom Guild integration** so the Band key is runtime-injected like every other credential — one integration deepens both sponsor stories at once. Free-tier note: Band retains data 24h — record the demo video same-day (you were doing that anyway).

## 4. The daily cycle (the autonomy story)

```
T−15 min   Guild time trigger fires → analysts run in parallel
T−10       analysts post VOTES to the Band trading-floor room
T−7        challenge round if a confident dissenter objects (§3)
T−5        PM tallies weighted vote · applies risk rules · posts decision memo
T          PM executes via Alpaca paper API (same time every day)
T+1 day    (next trigger, before analysis) Evaluator posts yesterday's grades
           to the room: computes delta → attributes → refines or maintains →
           mutates context → THEN today's cycle runs with the updated desk
```

No human is in the daily loop. The user's controls are the envelope (edit anytime), a kill switch, and the dashboard. Grading yesterday *before* trading today means every trade is made by a desk that has already digested its most recent mistake.

**Demo-hour note (decisive practical detail):** demos run 5–7 PM PDT — US equities close at 1 PM PDT. Therefore the live on-stage cycle runs on **crypto (BTC/ETH), which trades 24/7 on Alpaca paper** — the daily trigger fires for real, in front of judges. The equities mode is demonstrated by **replaying the last 10 trading days** through the same pipeline, which is also exactly how you show self-improvement without waiting ten days (§7).

## 5. Decision delta — the desk's ground truth

For each trading day:

```
delta = R_desk − R_tracker          over the outcome window (close-to-close)
```

`R_desk` = realized return of the positions the PM chose; `R_tracker` = return of just holding the benchmark. This definition is deliberately harsh: the desk earns credit only for decisions that beat *doing nothing sensible*. It cannot claim a +2% day when the tracker rose 3%.

**Attribution:** because every analyst filed a signed, uniform signal, the evaluator scores each one against what actually happened: an analyst's daily score is `signal_direction · realized_move`, confidence-weighted. The sentiment agent that shouted +0.8 on a stock that fell 2% takes the hit; the historical agent that flagged mean-reversion correctly gets the credit — regardless of whether the PM followed it.

## 6. The self-improvement system

Three loops, all persisted as mutations of Guild workspace context — the desk's evolution is literally its context changing, versioned and rollback-able on the control plane.

**Loop 1 — Weight learning (every day, numeric).** The PM's mixing weights update by exponentially-weighted performance: analysts that have been right lately gain influence, analysts that have been wrong lose it (multiplicative-weights style, floor at 0.1 so no agent is ever fully silenced — it may be right tomorrow). Implements the user's rule with damping: **negative delta → refine** (meaningful weight shift toward whoever was right), **strongly positive delta → maintain** (updates shrink; don't fix a working desk). All updates are deliberately dampened — one lucky or unlucky day moves weights a little, a *streak* moves them a lot. Daily P&L is noisy, and a desk that overreacts to noise is not learning, it's thrashing.

**Loop 2 — Playbook refinement (on negative delta, semantic).** After a losing day, the evaluator runs a critique pass on the worst-scoring analyst: it re-reads that analyst's rationale next to the actual outcome and rewrites the analyst's *playbook* — a per-agent instruction block in context. Concretely: *"You scored pre-market hype headlines as strong buys three times; all three faded by close. New rule: discount headline sentiment lacking volume confirmation from the real-time agent."* The next trigger runs the analyst with the amended playbook. This is the self-evolution the event is named for: the agent's operating instructions are rewritten by the system's own experience, and every amendment is visible as a context diff in Guild.

**Loop 3 — Escalation on persistent failure (weekly / on drawdown, structural).** If cumulative delta stays negative across a window, the problem isn't weights — it's the desk's composition. The evaluator escalates: universe-selector re-screens the stock list within the unchanged risk envelope, and (stretch, with Pioneer) the accumulated `(headline → realized move)` pairs become training data to **fine-tune the sentiment classifier on this desk's own history** — Pioneer's "inference that improves with your traffic," instantiated. The hierarchy mirrors a real fund: adjust conviction daily, coach analysts on losses, restructure the desk only on sustained failure.

**Metrics (each loop gets exactly one, all on the dashboard):** cumulative delta vs tracker ↑ · agent-weight evolution chart (visible reallocation of trust) · playbook diff log with the delta that triggered each amendment · post-restructure delta trend.

## 7. Making improvement visible in a 3-minute demo

Self-improvement over days must compress into minutes. The replay harness runs the full pipeline over the last ~10 trading days of real historical data, one day per second of demo time, with the dashboard animating: early days — wrong sentiment calls, negative deltas, weights shifting, a playbook amendment appearing in the log; later days — hit rate rising, cumulative delta curve crossing above the tracker. Then the live beat: the crypto trigger fires on stage, the committee's signals stream in, the PM executes a real paper trade, and the decision memo appears. Closing line: *"Every red day made the desk different. Here are the diffs."*

## 8. Sponsor grounding

**Guild.ai is the desk.** Not a wrapper — every load-bearing mechanism is a Guild primitive: the committee = workspace agents; the same-time-every-day trade = a **time trigger**; the envelope, weights, and playbooks = **workspace context** (mutation = evolution, with version history as the audit trail of learning); Alpaca wrapped as a **custom Guild integration** (`guild integration create alpaca --base-url https://paper-api.alpaca.markets --auth-scheme api-key`) so keys are runtime-injected and agents never see raw secrets — say this out loud to judges, it's Guild's core value proposition; the desk published to **Agent Hub** so a judge can install a hedge-fund-in-a-box with one click. Free-tier note: persistent triggers need the $20 plan — pay it or ask the Guild table for credits; test-mode manual firing is the fallback and is fine on camera.

**Replay QA proves the desk ships.** The dashboard (below) is a real consumer SaaS surface — onboarding wizard, live portfolio, charts. At ~3:15 PM: point qa.replay.io at the deployed URL (code `HACKATHON`), let it explore the wizard and dashboard journeys, fix every filed bug via the coding-agent handoff, re-run to **0 open**. Their rubric verbatim: "well-designed SaaS apps with QA completed and all discovered bugs fixed."

**Band is the trading floor.** The vote (§3) runs in a Band room: structured votes, @mention challenge rounds, the evaluator's public grading, and full human visibility — Band's exact pitch (agents + humans coordinating in governed rooms) as the desk's decision record. Wrapped as a custom Guild integration so the key is injected, not held. Targets Band's $1,000; Ofer Mendelevitch (Band) is judging.

**Pioneer (optional, cheap, high leverage):** GLiNER2 for sentiment classification today; desk-specific fine-tuning on collected outcome pairs as the Loop-3 stretch. One extra prize pool for one schema call.

**Senso (stretch — only if ahead of schedule):** the desk's **playbook registry**. Playbooks and risk rules live as verified content in Senso; the evaluator's amendments are *published* through it (versioned, approval-workflowed), and analysts fetch their current playbook with citations at cycle start. Senso's versioning turns the playbook history — the core self-improvement artifact — into an auditable, citable trail, and its $2,000-credit prize is the second-largest sponsor pool. ~45 min via CLI (`senso ingest`) + query API; $100 free credits. Skip if the core loops aren't done — a shallow Senso call impresses nobody.

**Actian VectorAI DB (last stretch):** the desk's **episodic memory**. Embed each completed trading day (news digest + market conditions + votes + outcome) into a local VectorAI DB; the historical agent retrieves the k most *similar* past days — "days that looked like today" — and cites their outcomes in its vote. Case-based memory that grows daily is a legitimate fourth improvement loop and VectorAI DB's anchor use case. Risk: the product launched days ago — confirm install at the Actian booth before committing; fallback is in-memory cosine similarity (keep the Actian story only if the DB actually runs).

## 9. Data & execution layer

| Need | Source | Access |
|---|---|---|
| Paper trading (stocks + crypto) | Alpaca Trading API (`paper-api.alpaca.markets`) | free, instant key, no funding |
| Prices, bars, snapshots | Alpaca Market Data API | free tier |
| Headlines for sentiment | Alpaca News API or Finnhub | free tiers, instant keys |
| Historical bars (replay harness) | Alpaca historical data | free tier |

One vendor covers execution + data + news; crypto support solves the demo-hour problem. **Fallback rule (protect the afternoon):** if any data call fights you for >20 minutes, switch that input to a bundled historical dataset — the learning loops are indifferent to where numbers come from, and the loops are what's being judged.

## 10. Front-end (required — it's Replay's scored artifact)

One polished page + wizard, nothing more: **(1)** onboarding wizard (4 questions → proposed tracker + universe → confirm); **(2)** desk dashboard — cumulative delta vs tracker chart, today's decision memo, three analyst cards (signal, confidence, one-line rationale), agent-weight evolution chart, playbook amendment log, kill switch; **(3)** replay mode — the 10-day animation. Real loading/empty/error states everywhere — Replay files bugs against sloppy states, and fixing them is the prize.

## 11. Build plan (now = 12:30 PM → submit 4:25 PM)

| Time | Goal |
|---|---|
| 12:30–1:00 | Keys: Alpaca paper + news, Guild CLI auth, Replay `HACKATHON`. Scaffold Next.js app, deploy skeleton to Vercel. `guild agent init` × 5. **Someone asks the Guild table about trigger credits.** |
| 1:00–1:30 | Envelope → universe-selector working; uniform signal schema fixed (do this first — everything depends on it) |
| 1:30–2:00 | Lunch. Kick off nothing blocking. |
| 2:00–2:50 | Three analysts + PM end-to-end on crypto paper trade, **voting through the Band room** (post → poll → tally; challenge round if time); evaluator computing delta + Loop 1 weights; context mutation verified in Guild UI |
| 2:50–3:20 | Loop 2 playbook critique; replay harness over 10 days of historical bars; dashboard charts wired |
| 3:20–3:50 | **Replay QA run → fix all bugs → re-run green.** Publish agents to Agent Hub. |
| 3:50–4:25 | 3-min video, README (+ this doc as `DESIGN.md`), public repo, tokensand form. **Submit by 4:25.** |

Team split (4): A = front-end/dashboard · B = Guild agents (analysts + PM) · C = evaluator + loops + replay harness · D = Alpaca layer, deploy, Replay QA, video.

**Cut order if behind:** Actian episodic memory → Senso playbook registry → Loop 3 → Pioneer → challenge round (keep simple voting) → live crypto beat (replay-only demo) → third analyst (ship sentiment + real-time only). Never cut: the delta definition, Loop 1, **the Band vote** (it's the decision record and the demo's best moment), the weight-evolution chart, the Replay QA pass.

## 12. Judging map & honesty notes

**Idea** — a desk graded against "doing nothing" that must earn its complexity daily. **Autonomy** — scheduled, unattended trade on live market data; human sets the envelope, never the trade. **Tool use** — Guild primitives are the mechanism, not the logo; Replay's rubric followed verbatim. **Self-evolving** — weights, playbooks, and composition all mutate from outcomes, with context diffs as receipts. **Presentation** — the cumulative-delta curve crossing the tracker, live on stage.

Honesty notes for the demo: say "paper trading" out loud; don't claim the strategy is profitable — claim the *system learns*, and show the diffs; damping means you show *direction* of improvement over 10 replayed days, not a guaranteed win rate. Judges reward a rigorous learning loop over a lucky backtest.