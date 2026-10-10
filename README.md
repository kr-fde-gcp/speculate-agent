# Market Pulse

An [ADK](https://google.github.io/adk-docs/) multi-agent system that tracks **weather**, **news**, and **current events**, then speculates — with explicit confidence levels and a falsifiable watch list — about how those signals could affect a company's stock price.

> **Not investment advice.** This is a research and reasoning exercise built on free public data. It produces scenarios and the arguments behind them, never recommendations or price targets. See [Honest limitations](#honest-limitations).

---

## Architecture

```
SequentialAgent "market_pulse"
│
├─ 1. scope_agent ─────────────── gemini-2.5-flash, no tools ──→ state["scope"]
│       Resolves "how's FedEx looking?" into a concrete research target:
│       ticker, facilities to check, search queries, benchmark, sensitivities.
│
├─ 2. ParallelAgent "signal_collectors"  ◄── all five run concurrently
│       ├─ weather_analyst  get_weather_snapshot, geocode_location  → weather_brief
│       ├─ news_analyst     get_news_headlines                      → news_brief
│       ├─ events_analyst   get_current_events, tone trend          → events_brief
│       ├─ market_analyst   quote, history, benchmark compare       → market_brief
│       └─ web_analyst      google_search (built-in, optional)      → web_brief
│
└─ 3. impact_agent ───────────── gemini-2.5-pro, reads all briefs ──→ final report
        Synthesis, scenarios, priced-in assessment, watch list.
```

**Why this shape:**

- **Scope first.** Four agents guessing independently at which ticker and which facilities to research produces incoherent briefs. One cheap pass fixes the target for everyone.
- **Collect in parallel.** The four data domains are independent, so wall-clock time is roughly the slowest branch instead of the sum of all five.
- **Synthesize last, on a stronger model.** Retrieval is easy; calibrated speculation is not. The reasoning model only runs once, on the step where quality actually matters.
- **State, not conversation.** Each collector writes to a named `output_key`. The synthesizer pulls all five in via `{key?}` placeholders. No agent has to read another's transcript.

---

## Data sources — all free, no API keys

| Signal | Source | Notes |
|---|---|---|
| Weather + forecast | Open-Meteo | Geocoding + 16-day forecast |
| Headlines | Google News RSS | 48h–7d window, per-item tagging |
| Current events + media tone | GDELT 2.0 DOC API | Global coverage volume and tone trend |
| Price, volatility, volume | Yahoo Finance chart endpoint | Stooq CSV fallback |
| Live grounding | Google Search | Built into Gemini via ADK |

The only dependency is `google-adk` itself. The entire data layer is **standard-library only**, which is what makes it testable without ADK installed.

---

## Setup

```bash
cd market_pulse
python3 -m venv .venv && source .venv/bin/activate   # Python 3.10+ required by ADK
pip install -r requirements.txt

cp .env.example market_pulse_agent/.env
# edit market_pulse_agent/.env and add your GOOGLE_API_KEY
```

## Run

```bash
# Visual trace — best way to watch the parallel fan-out and every tool call
adk web .

# Terminal
adk run market_pulse_agent

# Standalone CLI, no ADK UI
python run_pulse.py FDX
python run_pulse.py "How might this week's weather and news affect Delta?"
python run_pulse.py NVDA --verbose --save nvda_report.md
```

`--verbose` streams each tool call and each agent's individual brief to stderr, which is the fastest way to see where a conclusion came from.

## Test

No network, no API key, and no ADK needed — the tools package imports nothing beyond the standard library:

```bash
python3 -m unittest discover -s tests -t . -v   # 28 tests
python3 tests/check_prompt_placeholders.py      # prompt-template safety check
```

---

## What the output looks like

The final report follows a fixed structure:

| Section | Purpose |
|---|---|
| **Bottom Line** | The single most important finding, with overall confidence |
| **Evidence Summary** | Table of facts only — signal, data, source, recency |
| **Transmission Analysis** | Per driver: mechanism → direction → magnitude → horizon → confidence |
| **Scenarios** | Downside / Base / Upside with subjective likelihoods summing to ~100% |
| **What Is Likely Already Priced In** | Separates known-and-absorbed from potentially-unrecognised |
| **Falsifiable Watch List** | 5–8 concrete, checkable things that would confirm or kill the reasoning |
| **Confidence And Limitations** | What failed, what's thin, where the reasoning is weakest |
| **Disclaimer** | Verbatim, every run |

---

## Design decisions worth knowing about

**Tools never raise.** Every tool returns `{"status": "ok"|"error", ...}`. An exception inside a tool kills the invocation; an error dict lets the agent see the failure, report the gap, and carry on. Partial data with a stated gap beats no report.

**Heuristics are labelled as heuristics, in the data.** The news tool tags headlines with keyword-derived "lean" and risk categories. The tool's own output says *"Keyword-count heuristic only. Not a sentiment model; do not quote as sentiment data."* — so the warning travels with the data into the model's context rather than living only in a prompt the model may drift from.

**The baseline exists to constrain the speculation.** The market analyst runs so the synthesizer knows what the stock has *already* done. If a stock routinely moves 2% a day, a speculated 1% effect is noise — the prompt forces that comparison explicitly.

**A null result is a valid finding.** The prompts repeatedly license the agents to conclude "this is routine, nothing here is material." Most weather is not material to most companies. An agent that manufactures a thesis every run is worse than useless.

**No literal braces in prompts.** ADK treats `{...}` in an instruction as a state placeholder and raises at runtime if the key is missing. A JSON example in a prompt is therefore a latent crash. All placeholders use the optional `{key?}` form, and `tests/check_prompt_placeholders.py` enforces this — it's the single easiest way to break an ADK agent, and it only shows up at runtime.

**Google Search lives in its own agent.** Built-in tools can't be mixed with function tools in the same agent, so web grounding is a separate parallel branch. It degrades gracefully: if the import fails or you set `MARKET_PULSE_ENABLE_WEB_SEARCH=false`, the pipeline runs with four collectors.

**Caching.** All HTTP responses are cached in-process with a per-source TTL (5 min quotes, 10 min news, 30 min weather, 24 h geocoding), so a fan-out that asks for the same quote five times hits the network once.

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MARKET_PULSE_FAST_MODEL` | `gemini-2.5-flash` | The five collector agents |
| `MARKET_PULSE_REASONING_MODEL` | `gemini-2.5-pro` | The synthesis step |
| `MARKET_PULSE_ENABLE_WEB_SEARCH` | `true` | Toggle the Google Search collector |

---

## Honest limitations

Worth being blunt about, because the output can look more authoritative than it is:

- **No fundamentals.** No earnings, margins, balance sheet, valuation multiples, or guidance history.
- **No positioning data.** No options flow, short interest, institutional ownership, or order book.
- **Latency.** Every source is public and free. The market saw all of it first — by minutes at best.
- **Speculation is speculation.** The causal chain from "storm at a hub" to "stock moves X%" is genuinely weak. The agent is built to *say* that rather than paper over it, which is the main reason the scenario likelihoods are labelled subjective.
- **Unofficial endpoints.** The Yahoo chart endpoint is undocumented and rate-limits. Stooq fallback is end-of-day only. Always read the `source` field.
- **LLM speculation is not a model.** Scenario likelihoods are a language model's subjective judgement, not calibrated probabilities from a backtested model. They should not be treated as having predictive validity.

## Extending it

- **More collectors:** add an `LlmAgent` with its own `output_key` to the `collectors` list, then reference `{your_key?}` in `IMPACT_INSTRUCTION`.
- **Structured output:** give `impact_agent` a Pydantic `output_schema` for machine-readable JSON. Note that ADK disallows tools on an agent with an `output_schema`, so you'd add a formatting agent after it if you still want markdown.
- **Scheduling:** wrap `run_pulse.py` in cron for a pre-market brief.
- **Memory:** swap `InMemorySessionService` for a persistent service to track how speculations actually played out — which is the honest way to find out whether any of this has predictive value.
