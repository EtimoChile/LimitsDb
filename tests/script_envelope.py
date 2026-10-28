"""Script envelope of a fake dialect, for engine doubles that never reach a real client.

The markers are not SQL*Plus on purpose: a test that sees them in a script knows the text came from the engine,
not from the engine-independent core.
"""

from typing import Any

PREAMBLE = ["#stop-on-error", "#echo"]
INTERACTIVE_PREAMBLE = ["#stop-on-error", "#echo", "#accept-input"]
EPILOGUE = ["#end"]
ILM_FOOTER = ["#close"]


def ilm_header(schema: str) -> list[str]:
    return [f"#open {schema}"]


def ilm_table(owner: str, table_name: str, sql_block: str) -> list[str]:
    return [f"#table {owner}.{table_name}", sql_block, "#run"]


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

    ilm_script_header = staticmethod(ilm_header)
    ilm_script_table = staticmethod(ilm_table)

    @staticmethod
    def ilm_script_footer() -> list[str]:
        return list(ILM_FOOTER)


def script_envelope_methods() -> dict[str, Any]:
    """The same methods as keyword arguments for a ``SimpleNamespace`` double."""
    return {
        "script_preamble": lambda *, interactive: list(INTERACTIVE_PREAMBLE if interactive else PREAMBLE),
        "script_prompt": prompt,
        "script_epilogue": lambda: list(EPILOGUE),
        "ilm_script_header": ilm_header,
        "ilm_script_table": ilm_table,
        "ilm_script_footer": lambda: list(ILM_FOOTER),
    }
