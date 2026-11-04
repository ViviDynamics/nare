"""The schema correction budget: how many times a run may ask the model to fix
an answer that fails its schema, and which corrections are free.

A caller sets the budget with `--max-corrections` (`run(max_corrections=...)`).
The default, 1, is one correction and then `schema_violation`, as it always was.

A correction is free when the failing answer has fewer distinct errors than
every earlier failing answer in the run. Free corrections need a budget above
1: with 1, "free" would grant a second correction the caller did not allow.
Each free correction strictly lowers the run's fewest-errors mark, so a run
cannot loop on them.

Every correction names the errors an earlier answer made that this answer no
longer makes, so a fix to one field is not silently undone by a fix to another.

The surface for other modules is `charge()`, which grants or refuses a
correction for one failing answer's errors, the `Correction` it returns, and
`correction_text()` for the message. A check that supplies its own error codes
passes them to `charge()` in the same list the validator's messages use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nare.session import Session

SCHEMA_CORRECTIONS_DEFAULT = 1


def check_max_corrections(value: object) -> None:
    """Refuse a budget that is not a positive integer, before any turn."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("max_corrections must be a positive integer")


def used(s: Session) -> int:
    """The charged corrections this run has spent."""
    return s.schema_corrections


@dataclass(frozen=True)
class Correction:
    """One correction the run granted.

    `number` counts every correction in the run, free or charged, from 1.
    `codes` are the distinct errors this answer made, in the order the
    validator reported them. `earlier` are errors an earlier failing answer
    made that this answer does not; the correction tells the model to keep them
    correct.
    """

    number: int
    charged: bool
    codes: list[str] = field(default_factory=list)
    earlier: list[str] = field(default_factory=list)
    limit: int = SCHEMA_CORRECTIONS_DEFAULT


def charge(s: Session, errors: list[str], limit: int) -> Correction | None:
    """Grant a correction for this failing answer, or refuse it.

    Returns None when the budget is spent and the answer is not free: the caller
    then ends the run with `schema_violation`, and the session is unchanged.
    Otherwise the session's correction state is updated and the granted
    `Correction` is returned, and the caller appends the correction message.
    """
    codes = list(dict.fromkeys(errors))
    fewest = s.schema_fewest_errors
    free = limit > 1 and fewest is not None and len(codes) < fewest
    if not free and used(s) >= limit:
        return None
    earlier = [code for code in s.schema_errors_seen if code not in codes]
    for code in codes:
        if code not in s.schema_errors_seen:
            s.schema_errors_seen.append(code)
    s.schema_fewest_errors = len(codes) if fewest is None else min(fewest, len(codes))
    charged = not free
    if charged:
        s.schema_corrections += 1
    s.schema_retried = True
    s.schema_correction_log.append({"charged": charged, "codes": codes})
    return Correction(
        number=len(s.schema_correction_log),
        charged=charged,
        codes=codes,
        earlier=earlier,
        limit=limit,
    )


def correction_text(correction: Correction, errors: list[str], restated: str) -> str:
    """The message the model reads for a correction. The complaint is the
    validator's own list, duplicates and all, as it has always been stated."""
    text = (
        f"Your answer did not satisfy the required JSON Schema: {'; '.join(errors)}.\n"
    )
    if correction.earlier:
        text += (
            "Earlier answers broke these checks, and this answer does not. Keep "
            "these earlier fixes: " + "; ".join(correction.earlier) + ".\n"
        )
    return text + restated


def record(correction: Correction) -> dict[str, object]:
    """The correction as an event detail and as the result's `corrections` entry
    (the `charged` and `codes` keys are the ones the result carries)."""
    return {
        "number": correction.number,
        "charged": correction.charged,
        "codes": correction.codes,
        "earlier_corrections": correction.earlier,
        "limit": correction.limit,
    }
