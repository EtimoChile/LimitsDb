from typing import Any, Optional


def nvl(value: Optional[Any], default: Any) -> Any:
    """Returns the value if it is not None, otherwise returns the default value.
    Args:
        value: The value to evaluate.
        default: The value to return if `value` is None.
    Returns:
        The original value if not None, else the default."""
    return value if value is not None else default