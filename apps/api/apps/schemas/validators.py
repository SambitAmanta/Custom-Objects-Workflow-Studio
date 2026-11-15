import re2

MAX_PATTERN_LENGTH = 100

VALID_FIELD_TYPES = {
    "text",
    "number",
    "boolean",
    "date",
    "enum",
    "reference",
    "list",
}


def validate_regex_pattern(pattern: str) -> None:
    """Validates regex pattern using google-re2 to prevent ReDoS.
    Rejects patterns exceeding MAX_PATTERN_LENGTH or containing lookarounds/backreferences.
    """
    if not isinstance(pattern, str):
        raise ValueError("Pattern must be a string")

    if len(pattern) > MAX_PATTERN_LENGTH:
        raise ValueError(
            f"Pattern length ({len(pattern)}) exceeds maximum allowed ({MAX_PATTERN_LENGTH} chars)"
        )

    try:
        # re2 guarantees linear-time evaluation and rejects backtracking constructs
        re2.compile(pattern)
    except Exception as e:
        raise ValueError(f"Invalid or unsafe regex pattern: {e}") from e
