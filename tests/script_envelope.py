"""Script envelope of a fake dialect, for engine doubles that never reach a real client.

The markers are not SQL*Plus on purpose: a test that sees them in a script knows the text came from the engine,
not from the engine-independent core.
"""

from typing import Any

PREAMBLE = ["#stop-on-error", "#echo"]
INTERACTIVE_PREAMBLE = ["#stop-on-error", "#echo", "#accept-input"]
EPILOGUE = ["#end"]


def prompt(message: str) -> str:
    return f"#say {message}"


class ScriptEnvelope:
    """Mixin that gives a class-based double the script methods of ``DatabaseEngine``."""

    @staticmethod
    def script_preamble(*, interactive: bool) -> list[str]:
        return list(INTERACTIVE_PREAMBLE if interactive else PREAMBLE)

    script_prompt = staticmethod(prompt)

    @staticmethod
    def script_epilogue() -> list[str]:
        return list(EPILOGUE)


def script_envelope_methods() -> dict[str, Any]:
    """The same methods as keyword arguments for a ``SimpleNamespace`` double."""
    return {
        "script_preamble": lambda *, interactive: list(INTERACTIVE_PREAMBLE if interactive else PREAMBLE),
        "script_prompt": prompt,
        "script_epilogue": lambda: list(EPILOGUE),
    }
