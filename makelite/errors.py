"""Exceptions used to unwind the interpreter."""


class Die(Exception):
    """Stop make with the given exit status (after cleanup)."""

    def __init__(self, status):
        Exception.__init__(self, status)
        self.status = status


def format_loc(loc):
    if loc is None:
        return "make: "
    return "%s:%d: " % loc
