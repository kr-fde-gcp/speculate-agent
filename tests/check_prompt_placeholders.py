"""Guard against the single easiest way to break an ADK agent at runtime.

ADK treats any ``{...}`` in an ``instruction`` as a session-state placeholder
and raises when the key is absent. Literal braces -- a JSON example, a set
literal, a code snippet -- therefore crash the agent on its first turn, and
only at runtime.

This check asserts every brace token in every prompt is a deliberate
OPTIONAL placeholder (``{key?}``), which is safe even before upstream agents
have populated state. Run it in CI alongside the unit tests.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_pulse_agent import prompts  # noqa: E402

SAFE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\?\}")
TOKEN = re.compile(r"\{[^{}]*\}")


def main() -> int:
    problems = []
    checked = 0

    for name in sorted(dir(prompts)):
        if not name.isupper():
            continue
        value = getattr(prompts, name)
        if not isinstance(value, str):
            continue
        for token in TOKEN.findall(value):
            checked += 1
            status = "OK " if SAFE.fullmatch(token) else "BAD"
            print("  {0}  {1:<20} {2}".format(status, name, token))
            if status == "BAD":
                problems.append((name, token))

    print("\nChecked {0} brace token(s) across prompt constants.".format(checked))
    if problems:
        print("FAIL: literal braces will raise at runtime:")
        for name, token in problems:
            print("  - {0}: {1}".format(name, token))
        return 1
    print("PASS: every placeholder is a safe optional state injection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
