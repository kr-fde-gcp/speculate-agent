"""Runtime configuration, all overridable by environment variable."""

from __future__ import annotations

import os

# Cheap, fast model for the parallel collector agents.
FAST_MODEL = os.environ.get("MARKET_PULSE_FAST_MODEL", "gemini-2.5-flash")

# Stronger model for the synthesis/speculation step, where reasoning quality
# and calibrated uncertainty actually matter.
REASONING_MODEL = os.environ.get("MARKET_PULSE_REASONING_MODEL", "gemini-2.5-pro")

# Google Search grounding adds a live web collector. Disable it if your
# model/region does not support the built-in tool.
ENABLE_WEB_SEARCH = os.environ.get("MARKET_PULSE_ENABLE_WEB_SEARCH", "true").lower() not in (
    "0",
    "false",
    "no",
)

APP_NAME = "market_pulse"

DISCLAIMER = (
    "This is an automated, speculative research exercise built on public data. "
    "It is NOT investment advice, a price target, or a recommendation to buy, "
    "sell, or hold any security. Scenarios are hypotheses, not forecasts."
)
