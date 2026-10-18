"""Shared error type (BLUEPRINT "Engineering conventions": tolerate damage)."""

import logging

log = logging.getLogger("cyber_arche")


class ParseError(Exception):
    """Raised internally for corrupt input; callers log it and keep going."""

    def __init__(self, file_offset: int, reason: str) -> None:
        super().__init__(f"@{file_offset}: {reason}")
        self.file_offset = file_offset
        self.reason = reason
