from typing import Any, List, Optional, Iterable


def nvl(value: Optional[Any], default: Any) -> Any:
    """Returns the value if it is not None, otherwise returns the default value.
    Args:
        value: The value to evaluate.
        default: The value to return if `value` is None.
    Returns:
        The original value if not None, else the default."""
    return value if value is not None else default

from typing import Optional, Sequence, TypeVar

T = TypeVar('T')

def max_ignore_none(values: Sequence[Optional[T]]) -> Optional[T]:
    """
    Returns the maximum value from a sequence, ignoring None values.
    
    Raises:
        ValueError: If no non-None values are found.
        TypeError: If values are not mutually comparable.

    :param values: Sequence of optional values.
    :return: The maximum value or None if all values are None.
    """
    filtered = [v for v in values if v is not None]
    if not filtered:
        return None
    try:
        return max(filtered) # type: ignore
    except TypeError as e:
        raise TypeError("Values are not mutually comparable.") from e

def indent_lines(text: str, spaces: int) -> str:
    """
    Returns the input string with all lines after the first indented by the given number of spaces.

    :param text: Multiline string to process.
    :param spaces: Number of spaces to prepend to each line after the first.
    :return: Modified string with indentation applied.
    """
    return f"\n{" " * spaces}".join(text.splitlines())

def join_wrapped(connector: str, items: Iterable[str], max_line_length: int) -> str:
    """
    Joins items into a comma-separated string, wrapping lines when the max_line_length is exceeded.
    Lines after the first begin with ',' to indicate continuation.

    :param items: Iterable of elements to join (converted to strings).
    :param max_line_length: Max allowed characters per line before wrapping.
    :return: String with comma-separated items and continuation lines starting with ','.
    """
    lines: List[str] = []
    current_line = ""
    for item in map(str, items):
        candidate = (connector if current_line else "") + item
        if len(current_line) + len(candidate) > max_line_length:
            lines.append(current_line)
            current_line = connector + item
        else:
            current_line += candidate
    if current_line:
        lines.append(current_line)
    return "\n".join(lines)

def split_credentials(cred: str) -> tuple[str, str, str]:
    """
    Splits a credentials string in the format 'user/password@dsn' into its components.

    :param cred: Credentials string.
    :return: Tuple of (user, password, dsn).
    :raises ValueError: If the input format is incorrect.
    """
    try:
        user_pass, dsn = cred.split('@', 1)
        user, password = user_pass.split('/', 1)
        return user, password, dsn
    except ValueError:
        raise ValueError("Credentials must be in the format 'user/password@dsn'.")