# DeltaDesk — Phase 2: Three Analysts + Forecaster

Three analyst agents emit a uniform directional signal, and a forecaster tallies them
into one score per ticker using versioned, bounded weights.

```
news ──────┐
historical ─┼──> signals table ──> forecaster ──> forecasts table
realtime ──┘
```

Signal generation only — no order execution, no position sizing, no broker integration,
and no price targets. Paper-trading research.

## Agent workflow

The agents follow the same shape as the original `google-news-agent`: collect inputs,
evaluate candidate reads, select one final call, and emit enough trace data to improve the
process later.

| Agent | Collects | Candidate reads |
| ----- | -------- | --------------- |
| `news` | `google-news-agent` consolidated JSON | alternate mappings from upstream score/action into a DeltaDesk signal |
| `historical` | daily OHLCV bars | alternate trend equations |
| `realtime` | current quote | alternate momentum equations |
| `forecaster` | stored analyst signals | alternate forecast equations |

Every `Signal` and `Forecast` includes:

- `action`: explicit `BUY`, `SELL`, or `HOLD`
- `prompt_snapshot`: prompt text/source/hash in force
- `equation_snapshot`: selected mathematical strategy
- `model_snapshot`: decision provider, Gemini model, and thinking level
- `learning_snapshot`: performance-policy version and evidence available to the agent
- `agent_trace`: compact workflow trace with candidate reads/forecasts and selected output

## Signal contract

Every analyst emits the same shape, whatever it read to get there:

```json
{
  "ticker": "GOOGL",
  "source": "historical",
  "action": "BUY",
  "direction": 0.2948,
  "confidence": 0.7278,
  "rationale": "Positive slope and MA10 > MA30 confirm an uptrend.",
  "provenance": {
    "source_run_id": "historical-fe6e95e9a473",
    "inputs_used": ["historical_bars[GOOGL]:90 closes from yahoo"],
    "degraded": false,
    "notes": ""
  },
  "deterministic": false,
  "model_snapshot": {
    "provider": "gemini",
    "model": "gemini-3.6-flash",
    "thinking_level": "high",
    "structured_output": true
  },
  "cycle": "2026-07-24T22Z"
}
```

`direction` is -1..+1, `confidence` is 0..1. The forecaster never needs to know how a
signal was derived — only how hard it points and how much to trust it.

## Gemini thinking decisions

Every live analyst and the forecaster use the same structured Gemini decision step.
Gemini sees the input summary and every allowed mathematical candidate, thinks internally,
selects one equation, and returns exactly:

- `action`: `BUY`, `SELL`, or `HOLD`
- `selected_equation`: one allowed equation for that agent
- `confidence`: 0..1
- `rationale` and grounded evidence

The default is `gemini-3.6-flash` with `high` thinking. A live run requires
`GEMINI_API_KEY`; it does not silently replace a failed Gemini decision with a local
heuristic. Hidden chain-of-thought is not stored. DeltaDesk stores the concise decision
rationale and model metadata needed for an audit.

Run live:

```bash
python run_agents.py all
```

For a reliable demo, use deterministic market/news inputs while still forcing all four
decisions through real Gemini:

```bash
MOCK_MODE=1 python run_agents.py all --gemini-thinking
```

Plain `MOCK_MODE=1` uses an explicitly labeled mock decision provider for offline tests.

### How each analyst derives its signal

| Source | Inputs | Method |
| ------ | ------ | ------ |
| `news` | existing `google-news-agent` output | Maps its consolidated decision — the agent itself is untouched, and this re-reads rather than re-analyzes |
| `historical` | 90 daily bars | Least-squares slope (60%) + MA10/MA30 relationship (40%), faded when the last move is a ≥2σ outlier |
| `realtime` | current quote | Mean of % vs open and % vs previous close; a volume anomaly raises confidence, never direction |

Mean reversion **fades** an overextended trend rather than flipping it — an extended
uptrend is still an uptrend. Volume affects only how much to trust a move, not its sign.

## Forecaster

```
contribution_i = weight_i × direction_i × confidence_i
score          = Σ contribution_i         (clamped to -1..+1)
direction      = UP if score > t, DOWN if score < -t, else FLAT
confidence     = agreement × mean_confidence × coverage
```

Weights come from `SIGNAL_WEIGHTS` and are **renormalized over whichever sources actually
reported**, so a missing analyst never quietly drags the score toward zero. It narrows the
base and is recorded as degraded provenance instead — `confidence` drops via `coverage`
while `score` stays honest.

`per_agent_contributions` is emitted so every forecast is fully attributable:

```
FORECAST GOOGL FLAT score=+0.0832 conf=0.397
  news        dir=+0.2195 conf=0.85 w=0.40 -> +0.0746
  historical  dir=-0.1408 conf=0.29 w=0.35 -> -0.0144
  realtime    dir=+0.2732 conf=0.34 w=0.25 -> +0.0230
```

## Daily performance learning

`daily_learning.py` runs at most once per UTC date. It evaluates the latest daily call
from each agent/ticker against the next stored trading close, then publishes a new,
versioned policy:

- `BUY` is rewarded when the next close rises, `SELL` when it falls
- `HOLD` is rewarded when the move stays inside `HOLD_BAND_PCT`
- every equation keeps an observation count, mean score, and hit rate
- Gemini receives this performance memory as a prior and may override it when today's
  evidence favors another analysis mode
- forecaster source weights move toward better-performing agents by at most
  `LEARNING_MAX_WEIGHT_STEP` per daily update
- predictions, outcomes, before/after policy snapshots, and policy versions remain
  auditable in the database

The learner changes data, not source code or prompt text. It will not score a call until
two relevant closes exist, and it refuses to use an exit close after the requested
learning date.

Run it manually:

```bash
python daily_learning.py
python daily_learning.py --show-policy
```

Repeating the first command on the same date returns the stored successful run without
applying the performance twice. `run_agents.py` activates the latest learned weights
before a cycle; explicit `--tune` flags are applied afterward and therefore remain useful
for controlled Guild.ai experiments.

## Improvability

Every behavioural knob is external, bounded, and recorded:

```bash
python run_agents.py forecast --list-tunables
python run_agents.py all --tune SIGNAL_WEIGHTS.news=0.5 --tune DIRECTION_THRESHOLD=0.2
python run_agents.py all --tune-file experiment.json
```

Eleven knobs are declared in `config.TUNABLES` with allowed ranges. Overrides are applied
**once, before any agent runs**, so weights stay static within a cycle, and out-of-bounds
or unknown keys raise rather than silently clamping — a tuner asking for something
impossible has a bug worth surfacing. Zeroing every weight at once is rejected.

`config_snapshot` is stamped onto every stored forecast, while `learning_snapshot`
identifies the policy version used. That keeps each outcome attributable to both the
settings and performance evidence that produced it.

### Prompt-improvable agents

Each DeltaDesk agent has an explicit system prompt in `prompts.py`, and every stored
signal/forecast carries a `prompt_snapshot` with the prompt text, source, and hash that
produced it. The prompt governs its structured Gemini thinking decision and gives a clean
surface for prompt experiments.

Inspect the active prompts:

```bash
python run_agents.py all --list-prompts
```

Run with prompt variants without editing code:

```bash
python run_agents.py all \
  --prompt-file historical=prompts/historical_v2.txt \
  --prompt-file forecaster=prompts/forecaster_v2.txt
```

Supported prompt agents are `news`, `historical`, `realtime`, and `forecaster`.

### Data-aware analysis modes

Each agent evaluates named mathematical strategies and, by default, selects one from the
data regime it sees. The trace records `selection_policy`, `selection_reason`, and
`selection_evidence`, while `equation_snapshot` records the formula actually selected.

Automatic choices currently use:

| Agent | Data-aware choice |
| ----- | ----------------- |
| `news` | Fades thin coverage; blends a strong action when its score is weak/conflicting |
| `historical` | Uses slope for short history, MA regime for stretched moves, blend otherwise |
| `realtime` | Uses the available reference or balances open and previous close |
| `forecaster` | Dampens analyst disagreement; separates low-confidence agreement for testing |

Inspect the available equations:

```bash
python run_agents.py all --list-equations --list-analysis-policies
```

Lock equations for a reproducible experiment:

```bash
python run_agents.py all \
  --equation historical=slope_only \
  --equation realtime=open_weighted \
  --equation forecaster=consensus
```

`--equation AGENT=NAME` automatically sets that agent to `configured`. Return it to
data-aware selection with `--analysis-policy AGENT=auto`. Policies can also be set with
`NEWS_ANALYSIS_POLICY`, `HISTORICAL_ANALYSIS_POLICY`, `REALTIME_ANALYSIS_POLICY`, and
`FORECASTER_ANALYSIS_POLICY`.

The automatic regime boundaries are also available through `--tune`:
`NEWS_THIN_COVERAGE_MAX`, `NEWS_ACTION_CONVICTION_MIN`, `NEWS_WEAK_SCORE_ABS`, and
`FORECAST_LOW_CONFIDENCE`. They are included in every forecast's config snapshot.

Available strategies:

| Agent | Choices |
| ----- | ------- |
| `news` | `weighted_score`, `action_conviction_blend`, `article_count_fade` |
| `historical` | `trend_blend`, `slope_only`, `ma_cross` |
| `realtime` | `balanced_momentum`, `previous_close_only`, `open_weighted` |
| `forecaster` | `confidence_weighted`, `direction_only`, `consensus` |

## Setup

```bash
cd deltadesk
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Python 3.11+. No credentials required — Yahoo's chart endpoint is keyless, and the news
analyst reuses the existing `google-news-agent` and its own `.venv`.

| Variable | Purpose | Default |
| -------- | ------- | ------- |
| `ACTIAN_DATABASE_URL` | Primary store | `sqlite:///deltadesk.db` |
| `DEFAULT_SYMBOLS` | Universe | `GOOGL` |
| `MOCK_MODE` | Deterministic offline data | off |
| `GEMINI_API_KEY` | Required for live Gemini decisions | none |
| `GEMINI_THINKING_MODEL` | Shared decision model | `gemini-3.6-flash` |
| `GEMINI_THINKING_LEVEL` | Reasoning depth: low, medium, high | `high` |
| `LEARNING_ENABLED` | Activate stored daily policies | `true` |
| `LEARNING_LOOKBACK_DAYS` | Rolling performance window | `60` |
| `LEARNING_MIN_OBSERVATIONS` | Evidence required to recommend an equation | `3` |
| `LEARNING_MAX_WEIGHT_STEP` | Maximum source-weight movement per day | `0.05` |
| `HOLD_BAND_PCT` | Next-close move treated as a correct hold | `0.5` |

## Run

```bash
python run_agents.py news
python run_agents.py historical
python run_agents.py realtime
python run_agents.py forecast
python run_agents.py all        # three analysts concurrently, then the forecaster
```

JSON goes to stdout, logs to stderr. `MOCK_MODE=1` makes every run byte-identical — the
mock series is generated from a per-ticker seed with no RNG.

## Guild.ai experiments

`guild.yml` defines `deltadesk:experiment`, a real Guild.ai operation around one complete
analyst-to-forecaster cycle. Guild records the source snapshot and all flags. The wrapper
emits forecast and per-agent metrics as Guild scalars and saves the full
`guild_result.json` trace as a run artifact.

Guild.ai 0.9 uses Python's legacy `imp` module, so run its CLI with Python 3.11 or
earlier. Keep it isolated from the main application environment:

```bash
python3.11 -m venv .guild-venv
source .guild-venv/bin/activate
pip install -r requirements-guild.txt
guild check
```

Verify the operation and run the automatic selector:

```bash
guild operations
guild run deltadesk:experiment --yes
guild runs info
```

Run three forecaster equation experiments over identical mock inputs:

```bash
guild run deltadesk:experiment \
  forecaster_policy=configured \
  forecaster_equation=confidence_weighted --yes
guild run deltadesk:experiment \
  forecaster_policy=configured \
  forecaster_equation=direction_only --yes
guild run deltadesk:experiment \
  forecaster_policy=configured \
  forecaster_equation=consensus \
  --yes
```

Compare the tracked inputs and outputs in the terminal or Guild UI:

```bash
guild compare -Sc -Fo experiment -t
guild view
```

The Guild operation defaults to deterministic mock market data and a copied news sample,
so equation comparisons use identical inputs and remain reproducible inside Guild's
isolated run directories. Use `run_agents.py all` for the live-data demo. Selected
automatic modes and reasons remain available in each Guild run's `guild_result.json`.
The result also carries each agent's `learning_snapshot`, so a before/after policy run is
visible and reproducible in Guild rather than being an invisible runtime mutation.

To prove Gemini reasoning inside a Guild run, set `gemini_reasoning=true`. The wrapper
reuses the neighboring `google-news-agent/.env` when present; otherwise export
`GEMINI_API_KEY`. The key is loaded at runtime and is not copied into the Guild run:

```bash
guild run deltadesk:experiment \
  mock_mode=true \
  gemini_reasoning=true \
  thinking_level=high \
  --yes
```

## Storage

| Table | Dedup key |
| ----- | --------- |
| `agent_runs` | `run_id` |
| `market_snapshots` | `(ticker, cycle)` |
| `historical_bars` | `(ticker, bar_date)` |
| `signals` | `(ticker, source, cycle)` |
| `forecasts` | `(ticker, cycle)` |
| `performance_outcomes` | `(subject_type, subject_id)` |
| `agent_policies` | `agent` |
| `daily_learning_runs` | `learning_date` |

A cycle is one UTC hour bucket (`2026-07-24T22Z`), overridable with `--cycle`. Re-running
an agent in the same cycle **updates** its row rather than duplicating it. Timestamps are
UTC throughout; contributions and the config snapshot persist as JSON.

## Tests

```bash
python -m pytest tests/ -q
```

Covering signal derivation per source, the weighted three-way tally, direction thresholds,
degraded provenance, dedup, run logging, next-close scoring, no-lookahead behavior,
daily idempotency, and bounded policy updates.

## Notes

**Phase 1.** The `agents/`, `database.py`, `config.py`, and `run_agents.py` the brief
assumed already existed did not; only `google-news-agent/` was present. The minimum
substrate those three analysts needed was built here, and the news agent was left
untouched as specified.

**Previous close.** Yahoo's `chartPreviousClose` is the close *before the chart range
begins* — a month back — not the prior session. Using it read a +0.65% day as -7.40% and
produced a maximally bearish signal from good data. The previous close is now taken from
the last completed session in the bar series.
