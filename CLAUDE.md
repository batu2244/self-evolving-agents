Build a simple Python project for **DeltaDesk** containing independent data-collection agents.

## Scope

Create agents that collect, process, and store market data, plus the news-analyst signal
sub-agent in `google-news-agent/`.

Do not create:

* The main trading agent
* Order execution
* Portfolio management
* A frontend
* Self-improvement logic yet

Trading *signals* are in scope for the news analyst only, under the rules in
**Safety and scope** below. Signals are never acted on automatically.

## Agents

Create:

1. `news_agent.py`

   * Collect financial news using Alpaca or Finnhub
   * Calculate basic sentiment from `-1` to `1`
   * Store the news, sentiment, confidence, timestamp, ticker, and raw response in Actian

2. `realtime_market_agent.py`

   * Collect current prices, quotes, volume, open, high, low, previous close, and VWAP
   * Support equities and crypto
   * Store everything in Actian

3. `historical_market_agent.py`

   * Collect 30–90 days of OHLCV data
   * Calculate returns, moving averages, volatility, volume averages, and trend slope
   * Store raw bars and calculated features in Actian

4. `market_context_agent.py`

   * Collect benchmark data such as SPY, QQQ, BTC/USD, or ETH/USD
   * Calculate benchmark return, average universe return, and numbers of rising and falling assets
   * Store everything in Actian

5. `memory_agent.py`

   * Read the collected data from Actian
   * Create a factual daily summary
   * Store the summary and references to its source records in Actian
   * Do not make predictions or recommendations

## Project structure

```text
deltadesk/
├── agents/
│   ├── news_agent.py
│   ├── realtime_market_agent.py
│   ├── historical_market_agent.py
│   ├── market_context_agent.py
│   └── memory_agent.py
├── database.py
├── config.py
├── run_agents.py
├── guild.yml
├── CLAUDE.md
├── requirements.txt
├── .env.example
├── tests/
└── README.md
```

Each agent may directly:

1. Call its API
2. Validate and process the response
3. Store the result in Actian
4. Record whether the run succeeded or failed

Do not create unnecessary provider, repository, service, frontend, or model folders.

## Guild.ai

Use Guild.ai as the agent orchestration and control layer when supported by the installed Guild version.

Create `guild.yml` with operations for:

```bash
guild run news
guild run realtime
guild run historical
guild run context
guild run memory
guild run collect-all
```

The `collect-all` operation should:

1. Run the news, real-time, historical, and context agents
2. Run independent agents concurrently when practical
3. Run the memory agent after the collection agents finish

Use Guild for:

* Running individual agents
* Passing configuration
* Tracking runs
* Capturing logs and metrics
* Reproducing agent executions
* Scheduling or triggers when supported

Do not invent Guild.ai commands or configuration fields. Inspect the installed Guild documentation, CLI help, examples, or project files before implementing Guild-specific behavior.

The Python scripts must also work without Guild:

```bash
python run_agents.py news
python run_agents.py realtime
python run_agents.py historical
python run_agents.py context
python run_agents.py memory
python run_agents.py all
```

## CLAUDE.md

Create a root-level `CLAUDE.md` containing these instructions:

```markdown
# DeltaDesk Claude Code Instructions

## Current phase

Build the market data-collection sub-agents, plus the news-analyst signal sub-agent.

The news analyst (`google-news-agent/google_news_agent.py`) is in scope and does produce a
BUY/SELL/HOLD signal for GOOGL from news flow. It is a signal generator only.

Do not create the main trading agent, order execution, portfolio management, frontend, voting system, evaluator, or self-improvement loop.

## Architecture

Keep the project simple.

Each agent should directly:

1. Fetch data
2. Validate and process it
3. Store it in Actian
4. Log its run

Do not add architectural layers unless they solve an immediate problem.

## Guild.ai requirement

Guild.ai is the preferred orchestration and agent-control layer.

Use Guild when implementing:

- Agent execution
- Run tracking
- Configuration and flags
- Logs and metrics
- Reproducible experiments
- Agent scheduling or triggers when supported
- Future coordination between agents

Maintain a valid `guild.yml` with operations for each agent and the complete collection pipeline.

Before adding Guild-specific configuration:

1. Check the installed Guild version.
2. Run relevant Guild CLI help commands.
3. Inspect available Guild examples or documentation.
4. Use only commands and fields confirmed to exist.
5. Do not fabricate Guild APIs, SDK methods, triggers, or YAML fields.

Keep every Python agent runnable directly without Guild so development and testing are not blocked.

## Actian requirement

Actian is the main persistence layer.

Use the `ACTIAN_DATABASE_URL` environment variable.

Store raw data, processed data, timestamps, agent run information, and source references.

Use SQLite only as a local fallback.

Do not pretend an Actian-specific feature works unless it has been tested with the available Actian product and driver.

## Safety and scope

This is a paper-trading research project.

Signal generation is in scope. A sub-agent may output a BUY/SELL/HOLD call with its
reasoning, provided it is clearly labelled as paper-trading research and carries the
evidence behind it.

Never add:

- Real-money trading
- Order-placement code, position sizing, or broker/exchange integration
- Any claim that output is investment advice for anyone else
- Price targets, predicted percentage moves, or guaranteed outcomes
- Hardcoded credentials
- Uncontrolled web scraping

Every signal-producing agent must:

- Ground its call in the source data it actually read, and say so
- Report provenance explicitly, so degraded or fallback output is never presented as the
  real model's work
- Expose the deterministic score behind a call alongside any model narrative, so the
  decision can be audited back to its inputs

## Development rules

- Use Python 3.11 or newer.
- Use environment variables for secrets.
- Use UTC timestamps.
- Prevent duplicate records.
- Add retries for temporary API failures.
- Include mock data mode.
- Add basic tests.
- Run tests before finishing.
- Keep implementations small and demo-ready.
```

## Technology

Use:

* Python 3.11+
* `asyncio`
* `httpx`
* `pydantic`
* `sqlalchemy`
* `python-dotenv`
* `tenacity`
* `pytest`

Use environment variables for:

```text
ACTIAN_DATABASE_URL
ALPACA_API_KEY
ALPACA_API_SECRET
FINNHUB_API_KEY
DEFAULT_SYMBOLS
DEFAULT_BENCHMARK
MOCK_MODE
```

Never hardcode credentials.

## Database

Use SQLAlchemy with `ACTIAN_DATABASE_URL`.

Create tables for:

* `agent_runs`
* `news`
* `sentiment`
* `market_snapshots`
* `historical_bars`
* `historical_features`
* `market_context`
* `daily_memories`

Store UTC timestamps and raw JSON responses.

Prevent duplicate news and historical bars.

Keep the Actian connection configurable because the exact Actian product and driver may vary.

Use SQLite as a development fallback:

```text
sqlite:///deltadesk.db
```

## Requirements

* Handle API failures and rate limits
* Retry temporary failures
* Log every agent run
* Prevent duplicate records
* Include deterministic mock mode
* Include basic tests
* Include setup instructions in the README
* Do not create a frontend
* Do not create the main trading agent

Generate all files, run the tests, fix errors, and show the final project structure.
