# Langfuse Guide — Finding and Reading Market Pulse LLM Usage

A simple reference for finding a Market Pulse run in Langfuse and understanding
what you see there. Examples use a real run, `RUN-D7751C86` (competitor
`ooredoo`, competitor run `CR-6694F489`).

---

## 1. How a run is organised in Langfuse

Think of it as folders inside folders:

```
Session  = RUN-D7751C86                      ← one Market Pulse run
├── Trace: omantel_reference                 ← Omantel preparation (Step 2), once per run
│     ├── Generation: omantel_enrichment     ← the LLM calls
│     └── ...
├── Trace: competitor_run  (CR-6694F489, ooredoo)
│     ├── Generation: competitor_classification   (Step 1)
│     ├── Generation: plan_matching               (Step 3)
│     ├── Generation: narrative_generation        (Step 6)
│     └── ...
├── Trace: competitor_run  (CR-..., vodafone)     ← one per competitor
└── Trace: run_report                             ← portfolio advice / report
```

| Market Pulse | Langfuse name | Example |
|---|---|---|
| Run ID | **Session ID** | `RUN-D7751C86` |
| Competitor run | a **trace** named `competitor_run` | tag `competitor_run:CR-6694F489` |
| Omantel preparation | a **trace** named `omantel_reference` | — |
| One LLM step | a **generation** inside a trace | `plan_matching` |

Every trace also carries **tags** you can filter by:

```
run:RUN-D7751C86
competitor_run:CR-6694F489
competitor:ooredoo
```

---

## 2. Finding a run in the Langfuse website

Open the project at `https://us.cloud.langfuse.com` (the `LANGFUSE_BASE_URL` in `.env`).

**See everything for one run**
1. Go to **Sessions**.
2. Search for the run ID, e.g. `RUN-D7751C86`.
3. Open it — you see all traces of that run in time order.

**See one competitor only**
1. Go to **Traces** (Tracing).
2. Filter by tag, e.g. `competitor:ooredoo` or `competitor_run:CR-6694F489`.

**See one step's LLM calls (e.g. all plan matching)**
1. Go to **Observations** / Generations.
2. Filter by name, e.g. `plan_matching`.

**Tip:** the run ID and competitor run ID are printed in every log line, e.g.

```
run=RUN-D7751C86 cr=CR-6694F489 stage=plan_matching | Step 3 complete: ...
```

Copy them from `logs/market_pulse.log` and paste into the Langfuse search.

---

## 3. What each number means

Each trace (and each generation) has these values in its **metadata**:

| Field | Meaning | Example |
|---|---|---|
| `requests` | How many questions the code asked | `145` |
| `cache_hits` | How many were answered from the Redis cache (no LLM call, free) | `37` |
| `llm_calls` | How many actually went to the LLM | `145` |
| `input_tokens` | Tokens sent to the LLM | `0` *(see section 6)* |
| `output_tokens` | Tokens the LLM sent back | `0` *(see section 6)* |
| `total_cost` | Estimated cost | `0.0` |

Rule of thumb: **`requests = cache_hits + llm_calls`** (when nothing fails).

**Example from `RUN-D7751C86`:**

```
omantel_reference  → requests 145, cache_hits 0,  llm_calls 145   (all fresh LLM calls)
competitor_run     → requests 37,  cache_hits 37, llm_calls 0     (Step 1 fully cached)
```

**Input / output:** click a generation to see exactly what was sent to the
LLM and what came back (only when `LANGFUSE_CAPTURE_IO=true`). This is the
fastest way to see *why* a step failed — e.g. the LLM answering in Markdown
instead of JSON.

**Cost** is not taken from Langfuse's model price list. The code calculates it
from `.env`:

```
LANGFUSE_INPUT_COST_PER_MILLION_TOKENS=1
LANGFUSE_OUTPUT_COST_PER_MILLION_TOKENS=5
```

So `cost = input_tokens × 1/1,000,000 + output_tokens × 5/1,000,000`.

---

## 4. Quick summary from the terminal

Instead of clicking around, print a run's totals:

```bash
./.venv/bin/python scripts/langfuse_run_usage.py RUN-D7751C86
```

Output:

```
Trace               Competitor run    Competitor      LLM calls   Cache hits   Requests   Input tok    Output tok   Total tok    Cost
omantel_reference   -                 -               145         0            145        0            0            0            0.0000
competitor_run      CR-6694F489       ooredoo         0           37           37         0            0            0            0.0000
TOTAL               -                 -               145         37           182        0            0            0            0.0000
```

As JSON (for scripts or spreadsheets):

```bash
./.venv/bin/python scripts/langfuse_run_usage.py RUN-D7751C86 --json
```

> **Important: always use `./.venv/bin/python`.**
> The project needs `langfuse` version 4. The system/anaconda Python has
> version 3, and running the script with it fails with:
> ```
> TypeError: ObservationsClient.get_many() got an unexpected keyword argument 'fields'
> ```
> Check your version with:
> ```bash
> ./.venv/bin/python -c "import importlib.metadata as m; print(m.version('langfuse'))"
> ```

Langfuse saves data a few seconds after it arrives — if a run just finished
and shows nothing, wait a little and try again.

---

## 5. Using the Langfuse API directly (optional)

Useful when you want raw data. Keys come from `.env`:

```bash
set -a; source .env; set +a

# All traces of one run
curl -s -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" \
  "https://us.cloud.langfuse.com/api/public/traces?sessionId=RUN-D7751C86&limit=50"

# Is Langfuse reachable?
curl -s https://us.cloud.langfuse.com/api/public/health
```

---

## 6. Known gaps (as of 2026-09-30)

| What you see | Why | Fix |
|---|---|---|
| **Tokens and cost are always 0** | The LLM proxy at `127.0.0.1:8801` returns `usage: {prompt_tokens: 0, completion_tokens: 0}`. The code passes on whatever the proxy reports. | Make the proxy return real token counts. No code change needed after that — tokens and cost flow into Langfuse automatically. |
| **Failed LLM calls are missing from `llm_calls`** | If the LLM reply cannot be read (e.g. Markdown instead of JSON), the call is not recorded. Example: `RUN-D7751C86` made 29 plan-matching calls but shows `llm_calls: 0` for the competitor run. | Code fix in `src/market_pulse/llm/cache.py` (`invoke_structured_cached`). |
| **Model name is empty** | The code does not send the model name to Langfuse, and `.env` has a placeholder `OPENAI_MODEL=the-model-name-your-proxy-accepts`. | Put the real model name in `.env` **and** pass it when recording generations in `src/market_pulse/llm/langfuse_metrics.py`. |

Check whether the proxy reports tokens:

```bash
set -a; source .env; set +a
curl -s http://127.0.0.1:8801/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d "{\"model\":\"$OPENAI_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Say OK\"}]}" \
  | python -c "import sys,json; print(json.load(sys.stdin)['usage'])"
```

- `{'prompt_tokens': 0, 'completion_tokens': 0, ...}` → proxy is not reporting tokens.
- Non-zero numbers → tokens will show up in Langfuse.

---

## 7. Settings reference (`.env`)

| Setting | What it does |
|---|---|
| `LANGFUSE_ENABLED=true` | Turns Langfuse on |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | Your project keys |
| `LANGFUSE_BASE_URL` | Langfuse server, e.g. `https://us.cloud.langfuse.com` |
| `LANGFUSE_ENVIRONMENT` | Label to separate `development` / `production` data |
| `LANGFUSE_CAPTURE_IO=true` | Also send the full LLM input and output (turn off if business data must not leave the app) |
| `LANGFUSE_INPUT_COST_PER_MILLION_TOKENS` | Price used to estimate input cost |
| `LANGFUSE_OUTPUT_COST_PER_MILLION_TOKENS` | Price used to estimate output cost |

---

## 8. Quick checklist when something looks wrong

1. **Find the run ID** in `logs/market_pulse.log` (`run=RUN-...`).
2. **Terminal summary:** `./.venv/bin/python scripts/langfuse_run_usage.py RUN-...`
3. **Open the session** in Langfuse → open the trace → click a generation.
4. **Look at the output** — is it the JSON the code expects, or free text?
5. **Compare numbers:** `requests` vs `cache_hits + llm_calls`. A big gap means calls failed.
