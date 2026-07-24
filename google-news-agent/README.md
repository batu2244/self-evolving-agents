# Google News Agent (Alphabet)

A single-file agent that collects recent Google News articles about Alphabet, classifies
them with **Pioneer**, summarizes them with **Google Gemini**, and prints valid JSON to
standard output.

Pipeline:

1. **Collect** — query 11 Google News RSS searches, keep articles from the look-back
   window, deduplicate by normalized title and URL.
2. **Analyze** — send each title + description to Pioneer's `/inference` endpoint using a
   multi-head classification schema. Drop anything not relevant to Alphabet or below the
   significance threshold.
3. **Summarize** — send the article and its analysis to Gemini for a factual summary and a
   short "why it matters" note.

All logs and errors go to **stderr**, so **stdout is always valid JSON**.

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

| Flag                  | Meaning                                                                        | Default |
| --------------------- | ------------------------------------------------------------------------------ | ------- |
| `--hours`             | Look-back window in hours                                                       | `24`    |
| `--limit`             | Max articles in the output                                                      | `10`    |
| `--min-significance`  | Drop articles scoring below this                                                | `0.4`   |
| `--max-analyze`       | Max articles sent to Pioneer, newest first (`0` = no cap)                       | `60`    |
| `--fallback-analysis` | If Pioneer returns nothing, classify locally with a keyword heuristic (opt-in)  | off     |
| `--verbose`           | Debug logging on stderr                                                         | off     |

A 24-hour run collects roughly 800 articles and ~590 after deduplication. Since Pioneer
bills per inference call, `--max-analyze` caps the batch at the 60 newest by default.

Separate the streams when you want to inspect both:

```bash
python google_news_agent.py --hours 24 --limit 10 > articles.json 2> run.log
```

## Output

A JSON array sorted by significance score (descending), then publication time (newest
first). Prints `[]` when nothing significant is found.

```json
[
  {
    "title": "Article title",
    "source": "Publisher",
    "published_at": "2026-07-24T18:30:00Z",
    "url": "https://news.google.com/rss/articles/...",
    "query": "Google antitrust",
    "summary": "Concise Gemini-generated summary.",
    "why_it_matters": "Why this may matter to Alphabet.",
    "significance_score": 0.85,
    "sentiment": "negative",
    "sentiment_score": -0.4,
    "category": "Regulation and antitrust",
    "affected_business_units": ["Google Search", "Google Ads"],
    "analysis_provider": "pioneer",
    "summary_provider": "gemini"
  }
]
```

`analysis_provider` is `pioneer` or `heuristic`; `summary_provider` is `gemini` or
`fallback`. The provenance is always explicit — heuristic output is never labelled as
Pioneer's.

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

Since every article is then rejected, the strict pipeline correctly prints `[]`. Use
`--fallback-analysis` to keep the pipeline demonstrable in the meantime — it substitutes a
deterministic local keyword classifier and tags the output `analysis_provider: "heuristic"`.
Remove the flag once the Pioneer plan is active.

## Constraints

- Google News **RSS** only — no scraping of rendered pages, no browser automation, and no
  attempt to bypass paywalls, CAPTCHAs, or access restrictions. Article URLs are Google
  News redirect links, left as served.
- Gemini is instructed to produce factual summaries only: no buy/sell/hold
  recommendations, no price predictions, no guaranteed outcomes, no unsupported facts.
- If Gemini fails for an article, the RSS description is used as the summary and
  `summary_provider` becomes `fallback`.
- Timestamps are UTC.

## Files

```text
google-news-agent/
├── google_news_agent.py
├── requirements.txt
├── .env.example
└── README.md
```
