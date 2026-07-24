# DeltaDesk — Phase 2: Three Analysts + Forecaster

Three analyst agents emit a uniform directional signal, and a forecaster tallies them
into one score per ticker using static, configured weights.

```
news ──────┐
historical ─┼──> signals table ──> forecaster ──> forecasts table
realtime ──┘
```

Signal generation only — no order execution, no position sizing, no broker integration,
and no price targets. Paper-trading research.

## Signal contract

Every analyst emits the same shape, whatever it read to get there:

```json
{
  "ticker": "GOOGL",
  "source": "historical",
  "direction": -0.1408,
  "confidence": 0.2922,
  "rationale": "slope +0.173%/day over 90 closes; MA10 < MA30.",
  "provenance": {
    "source_run_id": "historical-fe6e95e9a473",
    "inputs_used": ["historical_bars[GOOGL]:90 closes from yahoo"],
    "degraded": false,
    "notes": ""
  },
  "deterministic": true,
  "cycle": "2026-07-24T22Z"
}
```

`direction` is -1..+1, `confidence` is 0..1. The forecaster never needs to know how a
signal was derived — only how hard it points and how much to trust it.

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

## Improvability

Weights are **static** this phase — nothing learns or self-tunes. But every behavioural
knob is external, bounded, and recorded, which is what a future tuning agent needs:

```bash
python run_agents.py forecast --list-tunables
python run_agents.py all --tune SIGNAL_WEIGHTS.news=0.5 --tune DIRECTION_THRESHOLD=0.2
python run_agents.py all --tune-file experiment.json
```

Eleven knobs are declared in `config.TUNABLES` with allowed ranges. Overrides are applied
**once, before any agent runs**, so weights stay static within a cycle, and out-of-bounds
or unknown keys raise rather than silently clamping — a tuner asking for something
impossible has a bug worth surfacing. Zeroing every weight at once is rejected.

Critically, `config_snapshot` — all eleven values — is stamped onto every stored forecast.
Without that, an outcome could never be attributed back to the settings that produced it,
and no amount of scoring would teach a tuner anything.

What is still missing for a real improvement loop: **outcomes**. Nothing yet records what
the ticker actually did after a forecast. That is the input a tuner would need, and it is
deliberately out of scope here.

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

## Storage

| Table | Dedup key |
| ----- | --------- |
| `agent_runs` | `run_id` |
| `market_snapshots` | `(ticker, cycle)` |
| `historical_bars` | `(ticker, bar_date)` |
| `signals` | `(ticker, source, cycle)` |
| `forecasts` | `(ticker, cycle)` |

A cycle is one UTC hour bucket (`2026-07-24T22Z`), overridable with `--cycle`. Re-running
an agent in the same cycle **updates** its row rather than duplicating it. Timestamps are
UTC throughout; contributions and the config snapshot persist as JSON.

## Tests

```bash
python -m pytest tests/ -q     # 63 passed
```

Covering signal derivation per source, the weighted three-way tally, direction thresholds,
degraded provenance, dedup, run logging, and the tuning seam.

## Notes

**Guild.** `guild.yml` is deliberately absent. The installed `guild` is **Guild.ai CLI
v0.17.0** ("build, test, and deploy AI agents"), which is a different product from
**guildai**, the ML experiment-tracking tool whose `guild.yml` defines `guild run <op>`
operations. This CLI has no `run` command at all:

```
$ guild run
error: unknown command 'run'
```

Its commands are `agent`, `workspace`, `trigger`, `session`, `job`, `chat`, `mcp`, and so
on. Writing a `guild.yml` with an `operations:` block would be fabricating configuration
for a tool that never reads it, which CLAUDE.md explicitly forbids. The Python runners
above are the supported entry points. If Guild orchestration is wanted, the real path is
`guild agent` / `guild trigger` against the Guild.ai platform — a different design worth
scoping on its own.

**Phase 1.** The `agents/`, `database.py`, `config.py`, and `run_agents.py` the brief
assumed already existed did not; only `google-news-agent/` was present. The minimum
substrate those three analysts needed was built here, and the news agent was left
untouched as specified.

**Previous close.** Yahoo's `chartPreviousClose` is the close *before the chart range
begins* — a month back — not the prior session. Using it read a +0.65% day as -7.40% and
produced a maximally bearish signal from good data. The previous close is now taken from
the last completed session in the bar series.
