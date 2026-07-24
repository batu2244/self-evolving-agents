# News Analyst Agent — GOOGL

A single-file news-analyst sub-agent that covers one name: Alphabet (GOOGL). It collects
recent Google News coverage, classifies it with **Pioneer**, runs it through a trader
persona on **Google Gemini**, and emits a consolidated **BUY / SELL / HOLD** call as JSON.

It is a *signal generator*, not a trader: there is no order execution, no position sizing,
and no broker integration anywhere in the code. Output is paper-trading research.

Pipeline:

1. **Collect** — query 11 Google News RSS searches, keep articles from the look-back
   window, deduplicate by normalized title and URL.
2. **Classify** — send each title + description to Pioneer's `/inference` endpoint using a
   multi-head classification schema. Drop anything not relevant to Alphabet or below the
   significance threshold.
3. **Read as a trader** — a system-prompted Gemini persona issues a per-article call:
   signal, signal strength, confidence, time horizon, and reasoning.
4. **Consolidate** — a deterministic weighted score plus a Gemini synthesis pass produce
   one desk position for the window.

All logs and errors go to **stderr**, so **stdout is always valid JSON**.

## The trader persona

Step 3 replaces neutral summarization with an explicit system prompt (`TRADER_SYSTEM_PROMPT`
in `google_news_agent.py`). Print it with `--print-system-prompt`, or override it entirely
with `--system-prompt-file my_prompt.txt`.

The prompt encodes how the desk reasons:

- Trade the reaction, not the headline — ask what is genuinely new versus already priced in.
- Weight by proximity to revenue: Search and Ads first, then Cloud and YouTube, then Other
  Bets and Waymo.
- Primary events (earnings figures, rulings, guidance, shipping products) are strong
  evidence; opinion pieces and "is this a buy" content are weak.
- HOLD is a legitimate answer — most single stories do not justify a trade.

That last rule matters in practice. On a live run, the persona returned HOLD at
confidence 0.20 on *"Alphabet: Is the Stock a Buy on the Dip as Cloud Revenue Surges?"*,
reasoning that it was "an opinion piece from a retail-focused publisher, not a primary
source" — while issuing the window's only SELL on a concrete negative-free-cash-flow
report. A naive sentiment classifier would have scored the first article bullish.

Hard rules in the prompt: grounded in the supplied text, no price targets, no predicted
percentage moves, no guaranteed outcomes.

## How the final call is computed

Two independent paths, both reported:

- **`signal_score`** — deterministic and auditable. Each article contributes
  `signal_strength × significance × confidence`, normalized by total weight, giving a
  score from -1 to +1. Beyond `±--buy-threshold` (default 0.25) it implies BUY or SELL,
  otherwise HOLD.
- **`decision`** — the Gemini synthesis pass, which sees every per-article call *and* the
  mechanical aggregate. It may disagree with the aggregate, but the prompt requires it to
  say why in the thesis.

Reporting both means a suspicious call can always be traced back to the articles that
produced it.

## Setup

```bash
cd google-news-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # then fill in your keys
```

Requires Python 3.11+.

### Environment variables

| Variable           | Description                                        | Default                     |
| ------------------ | -------------------------------------------------- | --------------------------- |
| `GEMINI_API_KEY`   | Google Gemini API key                              | *(required for summaries)*  |
| `GEMINI_MODEL`     | Gemini model id                                    | `gemini-2.5-flash`          |
| `PIONEER_API_KEY`  | Pioneer API key (`pio_sk_...`)                     | *(required for analysis)*   |
| `PIONEER_MODEL`    | Pioneer model id or fine-tuned job id              | `fastino/gliner2-base-v1`   |
| `PIONEER_BASE_URL` | Pioneer API base URL                               | `https://api.pioneer.ai`    |

Credentials are read from the environment or `.env` only — nothing is hardcoded. `.env` is
gitignored; keep it that way.

## Run

```bash
python google_news_agent.py
python google_news_agent.py --hours 24 --limit 10
```

| Flag                     | Meaning                                                                       | Default |
| ------------------------ | ----------------------------------------------------------------------------- | ------- |
| `--hours`                | Look-back window in hours                                                      | `24`    |
| `--limit`                | Max articles carried into the decision                                         | `10`    |
| `--min-significance`     | Drop articles scoring below this                                               | `0.4`   |
| `--max-analyze`          | Max articles sent to Pioneer, newest first (`0` = no cap)                      | `60`    |
| `--buy-threshold`        | Weighted-score magnitude needed for BUY/SELL rather than HOLD                  | `0.25`  |
| `--system-prompt-file`   | Override the built-in trader system prompt                                     | —       |
| `--print-system-prompt`  | Print the system prompt to stderr and exit                                     | off     |
| `--fallback-analysis`    | If Pioneer returns nothing, classify locally with a keyword heuristic (opt-in) | off     |
| `--verbose`              | Debug logging on stderr                                                        | off     |

A 24-hour run collects roughly 800 articles and ~590 after deduplication. Since Pioneer
bills per inference call, `--max-analyze` caps the batch at the 60 newest by default.

Separate the streams when you want to inspect both:

```bash
python google_news_agent.py --hours 24 --limit 10 > articles.json 2> run.log
```

## Output

A single JSON object: the decision, the score behind it, and the articles that produced
it (sorted by significance, then newest). When no news clears the filters, the agent
returns a HOLD with an empty `articles` array and a thesis explaining why.

```json
{
  "ticker": "GOOGL",
  "as_of": "2026-07-24T20:57:12Z",
  "window_hours": 24,
  "mode": "paper-trading-research",
  "decision": {
    "action": "HOLD",
    "conviction": 0.4,
    "time_horizon": "swing",
    "thesis": "The most concrete financial data point is the reported negative free cash flow...",
    "key_drivers": ["Reported negative free cash flow amid AI spending spree"],
    "risks": ["The AI spending could lead to significant future revenue growth..."],
    "what_would_change_my_mind": "Clearer reporting showing a positive free cash flow trend...",
    "decision_provider": "gemini"
  },
  "signal_score": {
    "weighted_score": -0.0627,
    "implied_action": "HOLD",
    "article_signals": {"BUY": 0, "SELL": 1, "HOLD": 7},
    "articles_scored": 8,
    "total_weight": 1.245
  },
  "articles": [
    {
      "title": "Google reports negative free cash flow amid AI spending spree",
      "source": "Mashable",
      "published_at": "2026-07-24T19:41:00Z",
      "url": "https://news.google.com/rss/articles/...",
      "query": "Alphabet",
      "summary": "Concise factual summary.",
      "why_it_matters": "Why this matters to Alphabet.",
      "signal": "SELL",
      "signal_strength": -0.4,
      "confidence": 0.3,
      "time_horizon": "swing",
      "reasoning": "Negative free cash flow is a tangible financial negative...",
      "significance_score": 0.65,
      "sentiment": "neutral",
      "sentiment_score": 0.0,
      "category": "Gemini and AI",
      "affected_business_units": ["Google DeepMind"],
      "analysis_provider": "pioneer",
      "read_provider": "gemini"
    }
  ],
  "disclaimer": "Paper-trading research output..."
}
```

Provenance is always explicit: `analysis_provider` is `pioneer` or `heuristic`,
`read_provider` and `decision_provider` are `gemini` or `fallback`. Degraded output is
never labelled as though it came from the real thing.

## How Pioneer is called

Per the [Pioneer inference docs](https://docs.pioneer.ai/api-reference/inference/pioneer),
the request is `POST {PIONEER_BASE_URL}/inference` with an `X-API-Key` header and a unified
encoder schema of five classification heads:

| Head                      | Labels                                       | Maps to                    |
| ------------------------- | -------------------------------------------- | -------------------------- |
| `alphabet_relevance`      | relevant / not relevant to Alphabet          | `is_relevant`              |
| `significance`            | major / moderate / minor                     | `significance_score`       |
| `sentiment`               | negative / neutral / positive                | `sentiment`                |
| `category`                | the 13 categories                            | `category`                 |
| `affected_business_units` | 9 units (multi-label)                        | `affected_business_units`  |

GLiNER heads return labels with confidence scores, not raw floats, so the numeric fields
are derived: `significance_score` maps `major/moderate/minor` to `1.0/0.6/0.25` and scales
it by the head's own confidence; `sentiment_score` is the sentiment confidence signed by
direction (negative → negative). The response `result` shape for classification heads is
not pinned down in the public docs, so the parser accepts the plausible variants
(`{task: [{label, score}]}`, `[{task, label, score}]`, `{task: {label: score}}`, plain
strings, and batch-wrapped results) and skips the article if none match.

If Pioneer fails for one article, that article is skipped and the rest continue. A
non-retryable status (401/403/404/422) fails the whole stage immediately rather than
repeating a request that cannot succeed.

### Pioneer billing note

Pioneer inference requires a paid plan. Without one, `/inference` returns:

```json
{"detail": {"code": "card_required", "message": "To run inference on Pioneer, subscribe to the Hobby or Pro plan..."}}
```

Since every article is then rejected, the strict pipeline correctly returns a HOLD with no
articles. Use `--fallback-analysis` to keep the pipeline demonstrable in the meantime — it
substitutes a deterministic local keyword classifier and tags the output
`analysis_provider: "heuristic"`. Remove the flag once the Pioneer plan is active.

## Constraints

- **Signal generation only.** The agent emits a call; it does not place, route, or size
  orders, and contains no broker or exchange integration. Acting on the output is a
  separate, human decision.
- **Paper-trading research, not investment advice.** Every response carries `mode` and
  `disclaimer` fields stating so.
- Google News **RSS** only — no scraping of rendered pages, no browser automation, and no
  attempt to bypass paywalls, CAPTCHAs, or access restrictions. Article URLs are Google
  News redirect links, left as served.
- The agent reads **headlines and RSS descriptions only** — never full article text. Calls
  are therefore made on thin evidence, which is why the persona is instructed to cap
  confidence low on headline-only stories.
- No price, volume, or fundamentals data is used. This is one input to a decision, not a
  complete trading process.
- If Gemini fails for an article, the call falls back to the classifier's sentiment and
  significance alone, at confidence 0.2, with `read_provider: "fallback"`.
- Timestamps are UTC.

## Files

```text
google-news-agent/
├── google_news_agent.py
├── requirements.txt
├── .env.example
└── README.md
```
