"""Market Pulse -- an ADK multi-agent pipeline.

Topology::

    SequentialAgent "market_pulse"
      |
      +-- 1. scope_agent            (LlmAgent, no tools)      -> state["scope"]
      |
      +-- 2. ParallelAgent "signal_collectors"   [fan-out, concurrent]
      |        +-- weather_analyst   -> state["weather_brief"]
      |        +-- news_analyst      -> state["news_brief"]
      |        +-- events_analyst    -> state["events_brief"]
      |        +-- market_analyst    -> state["market_brief"]
      |        +-- web_analyst       -> state["web_brief"]     (optional)
      |
      +-- 3. impact_agent           (LlmAgent, reasoning model) -> final report

Why this shape:

* **Scope first.** The collectors need to agree on what they are researching.
  Resolving ticker, facilities, and search terms once -- cheaply -- stops four
  agents from each guessing differently.
* **Collect in parallel.** The four data domains are independent, so running
  them concurrently cuts wall-clock latency to roughly the slowest branch.
* **Synthesize last, on a stronger model.** Speculation is where reasoning
  quality and calibrated uncertainty actually matter, so that step gets the
  bigger model and all five briefs via session state.

The built-in Google Search tool cannot be mixed with function tools in the
same agent, so web grounding lives in its own collector branch rather than
being bolted onto the news analyst.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent

from . import prompts
from .config import ENABLE_WEB_SEARCH, FAST_MODEL, REASONING_MODEL
from .tools import (
    compare_to_benchmark,
    geocode_location,
    get_coverage_tone_trend,
    get_current_events,
    get_news_headlines,
    get_price_history,
    get_stock_quote,
    get_weather_snapshot,
)

# ---------------------------------------------------------------------------
# Stage 1 -- scope the request
# ---------------------------------------------------------------------------
scope_agent = LlmAgent(
    name="scope_agent",
    model=FAST_MODEL,
    description="Resolves a loose request into a concrete research target: ticker, facilities, search queries, sensitivities.",
    instruction=prompts.SCOPE_INSTRUCTION,
    output_key="scope",
)

# ---------------------------------------------------------------------------
# Stage 2 -- parallel signal collection
# ---------------------------------------------------------------------------
weather_analyst = LlmAgent(
    name="weather_analyst",
    model=FAST_MODEL,
    description="Checks weather at operationally important locations and judges disruption materiality.",
    instruction=prompts.WEATHER_INSTRUCTION,
    tools=[get_weather_snapshot, geocode_location],
    output_key="weather_brief",
)

news_analyst = LlmAgent(
    name="news_analyst",
    model=FAST_MODEL,
    description="Retrieves recent headlines and judges which stories carry genuinely new information.",
    instruction=prompts.NEWS_INSTRUCTION,
    tools=[get_news_headlines],
    output_key="news_brief",
)

events_analyst = LlmAgent(
    name="events_analyst",
    model=FAST_MODEL,
    description="Tracks macro, geopolitical, and sector events plus media tone trajectory.",
    instruction=prompts.EVENTS_INSTRUCTION,
    tools=[get_current_events, get_coverage_tone_trend],
    output_key="events_brief",
)

market_analyst = LlmAgent(
    name="market_analyst",
    model=FAST_MODEL,
    description="Establishes price, volatility, and relative-performance baseline to gauge what is already priced in.",
    instruction=prompts.MARKET_INSTRUCTION,
    tools=[get_stock_quote, get_price_history, compare_to_benchmark],
    output_key="market_brief",
)

collectors = [weather_analyst, news_analyst, events_analyst, market_analyst]

# Optional fifth collector using the built-in Google Search grounding tool.
# Kept in its own agent: built-in tools cannot share an agent with function
# tools. Degrades gracefully if the installed ADK or model lacks support.
if ENABLE_WEB_SEARCH:
    try:
        from google.adk.tools import google_search

        web_analyst = LlmAgent(
            name="web_analyst",
            model=FAST_MODEL,
            description="Grounds the picture in live web results and finds upcoming dated catalysts.",
            instruction=prompts.WEB_SEARCH_INSTRUCTION,
            tools=[google_search],
            output_key="web_brief",
        )
        collectors.append(web_analyst)
    except ImportError:  # pragma: no cover - depends on installed ADK build
        web_analyst = None

signal_collectors = ParallelAgent(
    name="signal_collectors",
    description="Runs the weather, news, events, market, and web analysts concurrently.",
    sub_agents=collectors,
)

# ---------------------------------------------------------------------------
# Stage 3 -- synthesis and speculation
# ---------------------------------------------------------------------------
impact_agent = LlmAgent(
    name="impact_agent",
    model=REASONING_MODEL,
    description="Synthesizes all briefs into calibrated scenarios, a priced-in assessment, and a falsifiable watch list.",
    instruction=prompts.IMPACT_INSTRUCTION,
    output_key="impact_report",
)

# ---------------------------------------------------------------------------
# Root agent -- what `adk web` / `adk run` discovers
# ---------------------------------------------------------------------------
root_agent = SequentialAgent(
    name="market_pulse",
    description=(
        "Tracks weather, news, and current events for a company, then speculates -- "
        "with explicit confidence and a falsifiable watch list -- about how those "
        "signals could affect its stock price. Research only, never advice."
    ),
    sub_agents=[scope_agent, signal_collectors, impact_agent],
)
