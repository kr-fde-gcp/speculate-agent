#!/usr/bin/env python3
"""Standalone CLI runner for the Market Pulse agent.

Lets you run the pipeline without the ADK dev UI:

    python run_pulse.py FDX
    python run_pulse.py "How might this week's weather and news affect Delta?"
    python run_pulse.py NVDA --save report.md

``adk web .`` from this directory gives you the visual trace instead, which is
the better way to see the parallel fan-out and each agent's tool calls.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import sys
from typing import Any

try:
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
except ImportError as exc:  # pragma: no cover
    sys.exit(
        "Missing dependency: {0}\nInstall with:  pip install -r requirements.txt".format(exc)
    )

from market_pulse_agent.agent import root_agent
from market_pulse_agent.config import APP_NAME

USER_ID = "local_user"
SESSION_ID = "market_pulse_cli"


async def _maybe_await(value: Any) -> Any:
    """Support both sync and async session-service APIs across ADK versions."""
    return await value if inspect.isawaitable(value) else value


async def run(prompt: str, verbose: bool) -> str:
    session_service = InMemorySessionService()
    await _maybe_await(
        session_service.create_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID
        )
    )

    runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=session_service)
    message = types.Content(role="user", parts=[types.Part(text=prompt)])

    final_text = ""
    async for event in runner.run_async(
        user_id=USER_ID, session_id=SESSION_ID, new_message=message
    ):
        author = getattr(event, "author", "?")

        if verbose:
            for call in event.get_function_calls() or []:
                print("  [{0}] -> tool {1}({2})".format(author, call.name, call.args), file=sys.stderr)

        if event.is_final_response() and event.content and event.content.parts:
            text = "".join(p.text or "" for p in event.content.parts).strip()
            if not text:
                continue
            if verbose:
                print("\n--- {0} ---\n{1}".format(author, text), file=sys.stderr)
            # The last final response in the sequence is the impact report.
            final_text = text

    return final_text


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Weather + news + events -> speculative stock-impact analysis."
    )
    parser.add_argument("target", help="Ticker, company name, or a full question.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Stream tool calls and each agent's brief to stderr.")
    parser.add_argument("--save", metavar="PATH", help="Also write the final report to this file.")
    args = parser.parse_args()

    prompt = args.target
    if len(prompt.split()) <= 4:
        prompt = (
            "Analyze {0}. Track current weather at its key operational sites, recent news, "
            "and relevant current events, then speculate on the potential stock price "
            "impact with explicit confidence levels.".format(prompt)
        )

    report = asyncio.run(run(prompt, args.verbose))
    if not report:
        sys.exit("No report produced. Re-run with --verbose to see where the pipeline stopped.")

    print(report)
    if args.save:
        with open(args.save, "w", encoding="utf-8") as fh:
            fh.write(report)
        print("\n[saved to {0}]".format(args.save), file=sys.stderr)


if __name__ == "__main__":
    main()
