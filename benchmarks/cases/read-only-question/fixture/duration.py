"""Durations written the way people write them in config files."""

from datetime import timedelta

UNITS = {"s": "seconds", "m": "minutes", "h": "hours"}


def parse_duration(text):
    """Turn a duration such as "90s", "5m" or "2h" into a timedelta."""
    number, unit = text[:-1], text[-1]
    if unit not in UNITS:
        raise ValueError(f"unknown unit in {text!r}; use s, m or h")
    return timedelta(**{UNITS[unit]: int(number)})
