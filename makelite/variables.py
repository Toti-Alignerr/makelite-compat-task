"""Variables, variable sets and their origins."""

O_DEFAULT, O_ENV, O_FILE, O_ENV_OVERRIDE, O_COMMAND, O_OVERRIDE, O_AUTOMATIC = range(7)

ORIGIN_NAMES = {
    O_DEFAULT: "default",
    O_ENV: "environment",
    O_FILE: "file",
    O_ENV_OVERRIDE: "environment override",
    O_COMMAND: "command line",
    O_OVERRIDE: "override",
    O_AUTOMATIC: "automatic",
}

EXPORT_DEFAULT, EXPORT_YES, EXPORT_NO = 0, 1, 2


def exportable_name(name):
    if not name or not (name[0] == "_" or name[0].isalpha() and name[0].isascii()):
        return False
    for c in name[1:]:
        if not (c == "_" or (c.isascii() and c.isalnum())):
            return False
    return True


class Variable:
    __slots__ = ("name", "value", "recursive", "origin", "export", "append",
                 "loc", "expanding", "per_target", "exportable", "conditional")

    def __init__(self, name, value, origin, recursive, loc=None):
        self.name = name
        self.value = value
        self.origin = origin
        self.recursive = recursive
        self.loc = loc
        self.export = EXPORT_DEFAULT
        self.append = False
        self.expanding = False
        self.per_target = False
        self.conditional = False
        self.exportable = exportable_name(name)


class VarSet(dict):
    """A mapping of name -> Variable."""

    def define(self, name, value, origin, recursive, loc, env_overrides=False):
        v = self.get(name)
        if v is not None:
            if env_overrides and v.origin == O_ENV:
                v.origin = O_ENV_OVERRIDE
            if origin >= v.origin:
                v.value = value
                v.loc = loc
                v.origin = origin
                v.recursive = recursive
            return v
        v = Variable(name, value, origin, recursive, loc)
        self[name] = v
        return v
