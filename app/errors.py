"""Shared exception types."""
from __future__ import annotations


class PermanentSendError(Exception):
    """A send that retrying cannot fix (bad token, closed 24h window, invalid number).

    The job goes straight to the 'dead' list so you can fix the cause and retry it from /admin.
    """


class Deferred(Exception):
    """Not an error: the job should wait until `until` (UTC ISO time), for example during quiet hours."""

    def __init__(self, until: str):
        super().__init__(until)
        self.until = until
