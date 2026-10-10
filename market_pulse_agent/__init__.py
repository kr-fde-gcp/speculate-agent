"""Market Pulse agent package.

``adk web`` and ``adk run`` discover the agent by importing this package and
reading ``agent.root_agent``.

The import is guarded so the standard-library-only ``tools`` subpackage stays
importable (and unit-testable) on a machine with no ADK installed. If ADK is
missing, ``market_pulse_agent.agent`` simply will not be bound -- which is
exactly what the offline test suite needs.
"""

try:
    from . import agent  # noqa: F401
except ImportError:  # pragma: no cover - ADK not installed
    agent = None  # type: ignore[assignment]

__all__ = ["agent"]
