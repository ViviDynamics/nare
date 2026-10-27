"""Small helpers for the expense report."""

import re


def format_bytes(size):
    """Return a size in bytes as a short human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.0f} TB"


def parse_csv_line(line):
    """Split one CSV line into its fields, without surrounding spaces."""
    return [field.strip() for field in line.split(",")]


def slugify(text):
    """Lower-case text and join its words with hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def total(values):
    """Add up numbers written as strings."""
    return sum(float(value) for value in values)
