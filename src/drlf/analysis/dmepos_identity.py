from __future__ import annotations


def postgres_array(values: list[str | int]) -> str:
    """Encode already-validated numeric identifiers as a PostgreSQL array literal."""
    rendered = [str(value) for value in values]
    if any(not value.isascii() or not value.isdigit() for value in rendered):
        raise ValueError("DMEPOS identity scope contains a nonnumeric identifier")
    return "{" + ",".join(rendered) + "}"
