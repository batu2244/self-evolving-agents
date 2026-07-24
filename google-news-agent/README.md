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
| `ACTIAN_VECTORAI_URL`     | Actian VectorAI gRPC endpoint               | `localhost:6574`            |
| `ACTIAN_VECTORAI_API_KEY` | Actian API key, if auth is enabled          | *(none)*                    |
| `ACTIAN_EMBED_MODEL`      | Embedding model for stored records          | `gemini-embedding-001`      |

Credentials are read from the environment or `.env` only — nothing is hardcoded. `.env` is
gitignored; keep it that way.

### Actian VectorAI

Persistence is Actian VectorAI, run locally in Docker:

```bash
docker pull actian/vectorai:latest
docker run -d --name vectorai \
  -v "$(git rev-parse --show-toplevel 2>/dev/null || pwd)/local_data:/var/lib/actian-vectorai" \
  -p 6573-6575:6573-6575 \
  -e ACTIAN_VECTORAI_ACCEPT_EULA=YES \
  actian/vectorai:latest
```

REST is on 6573, gRPC on 6574 (what the client uses), the local UI on 6575. Database
files land in `local_data/` in the project root, which is gitignored.

> The vendor quickstart mounts `/var/lib/vectorai`. That is not the path the server
> writes to — it uses `/var/lib/actian-vectorai`, as above. With the quickstart path the
> volume is silently ignored and the data lives only inside the container.

```bash
python actian_store.py health     # verify the connection and create collections
```

The client needs `grpcio>=1.81.0`; the version pinned in `requirements.txt` covers it.

### Known limitation: restarts drop stored points

VectorAI 1.0.2 does not recover collections across a server restart. After
`docker restart`, `collections.list()` still returns the collections and `get_info`
still reports the right `vectors_count`, but every point operation — `count`, `scroll`,
`search`, even `upsert` — fails with `CollectionNotFoundError`. This reproduces on a
bare four-dimensional collection with a Docker named volume, so it is the server, not
this project's schema or mount.

`ensure_collections()` therefore probes each collection and recreates any that cannot
serve point operations, logging a warning and listing them in the storage receipt under
`repaired_collections`. That keeps the agent working across restarts, at the cost of the
points that collection was holding. Treat the store as durable within a server lifetime,
not across restarts, and re-run the agent to repopulate.

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
| `--check-pioneer`        | Diagnose Pioneer key, model, and inference access, then exit                    | off     |
| `--fallback-analysis`    | If Pioneer returns nothing, classify locally with a keyword heuristic (opt-in) | off     |
| `--store`                | Persist the run to Actian VectorAI                                             | off     |
| `--actian-url`           | Override the VectorAI endpoint                                                 | —       |
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

### Diagnosing Pioneer

```bash
python google_news_agent.py --check-pioneer
```

This checks the model id against `GET /base-models`, then runs a real inference probe, and
prints a verdict. Note that `/base-models` is a **public** endpoint — it returns 200 with no
key at all — so it validates the model id and reachability, *not* your key. Only an actual
`/inference` call proves key validity and entitlement, which is why the probe exists.

The same catalog check runs as a preflight before every batch, so a wrong `PIONEER_MODEL`
is caught once with the list of valid encoder models, rather than as N identical failures.

### Two undocumented Pioneer behaviours

Both found by probing the live API; neither appears in the docs, and both fail *silently*
by returning `{"categories": []}` — a 200 response with no predictions rather than an error.

- **`top_k` breaks classification.** The fine-tuning guide lists `top_k` as a valid key on
  a classification entry. Sending it returns an empty result. Omitted here.
- **`multi_label: true` is unsupported on `gliner2-base-v1`.** Same empty result. True
  multi-label tagging is therefore unavailable, so business units are asked as one binary
  `"affects X" / "does not affect X"` head per unit, which does work.

The real response shape is also undocumented and nests one level deeper than you would
guess from the endpoint reference:

```json
{"result": {"data": {"sentiment": {"label": "positive", "confidence": 1.0}}}}
```

A third issue was on our side: Google News RSS descriptions are usually the headline
repeated with the publisher appended, so the classifier was receiving the title twice.
That duplication alone moved one headline's significance from `minor` (0.79) to `major`
(0.92). `classification_text()` now appends the description only when it carries text the
title does not.

### Known limitation: zero-shot classification quality

Pioneer inference now works end-to-end (`pioneer_status: "ok"`), but the *quality* of
zero-shot `gliner2-base-v1` on these abstract judgments is poor. From a live 60-article
run:

- **Significance is saturated.** Every article scored 0.998–1.000. The head answers
  "major" to essentially everything, which makes `--min-significance` ineffective as a
  filter.
- **Business units over-fire.** Mean 6.9 of 9 units flagged per article. A
  `UNIT_CONFIDENCE_FLOOR` of 0.7 barely helps — the model is confidently wrong.
- **Categories drift.** *"Tom Holland becomes first 'Hot Ones' guest to vomit"* was
  classified relevant to Alphabet, significance 1.0, category "Gemini and AI", 9 units
  affected.

This is expected of the model rather than a bug: the catalog describes it as *"Named
entity recognition; zero-shot span extraction"*. NER is what it is built for, and it does
that well — it cleanly extracted `Alphabet` and `Google Cloud` as organizations at ≥0.99.
Multi-head abstract classification is a stretch for it without fine-tuning.

The trader stage absorbs a lot of this. The Tom Holland article came back HOLD at
confidence 1.0 with the reasoning *"entertainment news with no financial impact"* — the
Gemini persona rejected what the classifier let through. That is defense in depth working,
but it means the significance weighting in `signal_score` is currently carrying less
information than it appears to.

Three ways forward, in increasing order of effort:

1. **Fine-tune.** Pioneer supports it (`POST /felix/training-jobs`); a few hundred labeled
   headlines would fix significance and category directly. This is the intended path.
2. **Narrow Pioneer's job** to relevance and entity extraction, and let Gemini assign
   significance and category as part of the trader read.
3. **Run `--fallback-analysis`**, whose keyword heuristic is cruder but at least produces
   a spread of significance scores.

### Pioneer billing

Inference requires an active Hobby or Pro plan. Without one, `/inference` returns HTTP 403
`card_required` for *every* request — including the quickstart's own verbatim NER example,
so a 403 is never a sign of a malformed request. A useful distinction when debugging: an
**invalid** key returns `401 Invalid API key`, while a valid-but-unentitled key returns
`403 card_required`, which confirms the key itself is good.

Every response carries a `pioneer_status` field recording exactly what happened
(`ok`, `not_configured`, `preflight_failed: ...`, `inference_failed: ...`,
`no_usable_predictions`), so a degraded run is never silently indistinguishable from a
clean one.

`--fallback-analysis` substitutes a deterministic local keyword classifier and tags the
output `analysis_provider: "heuristic"`, keeping the pipeline runnable when Pioneer is
unavailable.

## Storage

`--store` writes the run to Actian VectorAI. Three collections, all 768-dimensional with
cosine distance:

| Collection            | One point per            | Point ID                                     |
| --------------------- | ------------------------ | -------------------------------------------- |
| `deltadesk_articles`  | analyzed article         | UUIDv5 of the canonical article URL           |
| `deltadesk_decisions` | run's desk decision      | UUIDv5 of ticker + `as_of` + window           |
| `deltadesk_runs`      | run log entry            | UUIDv5 of the run id                          |

IDs are derived from content, not generated, so a re-run updates the existing record
rather than duplicating it. Article URLs are canonicalized (lowercased host, query string
and fragment stripped) before hashing, so `?utm_source=` variants of one story collapse
into a single point.

Each decision point stores the deterministic `signal_score` alongside the model's
narrative, so any call can be audited back to the numbers that produced it. Every point
also records `embedding_provider`, so records written with the offline fallback embedder
are never mistaken for the real model's output.

Storage is best-effort and never costs you a run: if the database is unreachable, the
agent still prints its JSON, with the failure reported in a `storage` field.

```bash
python google_news_agent.py --hours 24 --limit 10 --store

python actian_store.py health
python actian_store.py store sample_output.json
python actian_store.py search "antitrust ruling" --collection deltadesk_articles
python actian_store.py recent
```

### Embeddings

VectorAI stores and retrieves vectors; it does not produce them. Text is embedded with
Gemini `gemini-embedding-001` at 768 dimensions when `GEMINI_API_KEY` is set. Without a
key the store falls back to a deterministic local hash embedding — stable and offline, so
the storage path stays testable, but weak semantically. The provider used is recorded on
every point.

## Tests

```bash
python -m pytest test_actian_store.py -v
```

The pure-logic tests (ID derivation, embedding determinism, failure handling) always run.
The round-trip tests need a live VectorAI and skip cleanly when one is not reachable. They
use the hash embedder, so they require no API key and cost nothing.

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
├── google_news_agent.py     # collect -> analyze -> read -> decide -> JSON
├── actian_store.py          # Actian VectorAI persistence + query CLI
├── test_actian_store.py
├── requirements.txt
├── .env.example
└── README.md
```
