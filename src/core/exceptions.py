"""Project-wide custom exceptions."""

from __future__ import annotations


class ZeroDataLossError(RuntimeError):
    """Raised when an export would contain fewer rows than positions detected.

    This is the hard guard for Side-Condition (1): the number of output rows
    must never fall below the number of positions the parser/reconciler found
    in the source document. If it does, the file is NOT written.
    """
