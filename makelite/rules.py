"""The file database, explicit rules, pattern rules and implicit search."""

import os

from .text import (BLANKS, next_token, find_char_unquote, find_percent,
                   pattern_matches, patsubst_expand, strip_space)
from .variables import VarSet, O_AUTOMATIC, O_OVERRIDE, O_COMMAND, O_ENV_OVERRIDE

CS_NOT_STARTED, CS_DEPS_RUNNING, CS_RUNNING, CS_FINISHED = range(4)

NONEXISTENT = -1
NEW_MTIME = 1 << 62

COMMANDS_SILENT, COMMANDS_RECURSE, COMMANDS_NOERROR = 1, 2, 4


class Commands:
    def __init__(self, loc, lines):
        self.loc = loc            # (filename, lineno) of the first line
        self.lines = lines        # list of (text, lineno)
        self.chopped = None
        self.lines_flags = None
        self.any_recurse = False

    def chop(self):
        if self.chopped is not None:
            return
        self.chopped = list(self.lines)
        flags_list = []
        any_recurse = False
        for text, _ in self.chopped:
            flags = 0
            i = 0
            while i < len(text) and text[i] in " \t-@+":
                c = text[i]
                if c == "+":
                    flags |= COMMANDS_RECURSE
                elif c == "@":
                    flags |= COMMANDS_SILENT
                elif c == "-":
                    flags |= COMMANDS_NOERROR
                i += 1
            if not (flags & COMMANDS_RECURSE) and (
                    "$(MAKE)" in text[i:] or "${MAKE}" in text[i:]):
                flags |= COMMANDS_RECURSE
            flags_list.append(flags)
            any_recurse = any_recurse or bool(flags & COMMANDS_RECURSE)
        self.lines_flags = flags_list
        self.any_recurse = any_recurse

    def raw_text(self):
        return "\n".join(t for t, _ in self.lines)


class Dep:
    __slots__ = ("file", "name", "ignore_mtime", "changed")

    def __init__(self, file=None, name=None, ignore_mtime=False):
        self.file = file
        self.name = name
        self.ignore_mtime = ignore_mtime
        self.changed = False

    def dep_name(self):
        return self.file.name if self.file is not None else self.name


class DepLine:
    """An unparsed prerequisite list recorded while reading."""

    __slots__ = ("text", "staticpattern", "stem", "has_cmds")

    def __init__(self, text, staticpattern=False, stem=None):
        self.text = text
        self.staticpattern = staticpattern
        self.stem = stem
        self.has_cmds = False


class File:
    def __init__(self, name):
        self.name = name
        self.hname = name
        self.vpath_name = None
        self.deps = []
        self.deplines = []
        self.last_dep_has_cmds = False
        self.cmds = None
        self.stem = None
        self.also_make = []
        self.is_target = False
        self.phony = False
        self.precious = False
        self.intermediate = False
        self.secondary = False
        self.cmd_target = False
        self.dontcare = False
        self.double_colon = None
        self.prev = None
        self.tried_implicit = False
        self.updated = False
        self.update_status = -1
        self.command_state = CS_NOT_STARTED
        self.considered = False
        self.updating = False
        self.last_mtime = None          # None = unknown
        self.mtime_before_update = None
        self.parent = None
        self.variables = None
        self.pat_variables = None
        self.pat_searched = False
        self.command_flags = 0
        self.ignore_vpath = False


class PatternRule:
    def __init__(self, targets, deps_text, cmds, terminal):
        self.targets = targets          # pattern strings (unquoted)
        self.percents = []
        for t in targets:
            self.percents.append(t.index("%") if "%" in t else -1)
        self.deps_text = deps_text      # None when the rule has no prerequisites
        self.cmds = cmds
        self.terminal = terminal
        self.in_use = False


class PatternVar:
    def __init__(self, target, percent, name, value, flavor, origin, loc,
                 exported):
        self.target = target
        self.percent = percent
        self.name = name
        self.value = value
        self.flavor = flavor
        self.origin = origin
        self.loc = loc
        self.exported = exported


def get_next_word(s, i):
    """Return (start, end) of the next word as pattern_search sees it."""
    n = len(s)
    while i < n and s[i] in BLANKS:
        i += 1
    if i >= n:
        return None
    beg = i
    while i < n:
        c = s[i]
        if c in BLANKS:
            return beg, i
        if c == "$":
            i += 1
            if i >= n:
                return beg, i
            c2 = s[i]
            if c2 in "({":
                closep = ")" if c2 == "(" else "}"
                count = 0
                i += 1
                while i < n:
                    if s[i] == c2:
                        count += 1
                    elif s[i] == closep:
                        count -= 1
                        if count < 0:
                            i += 1
                            break
                    i += 1
                continue
            i += 1
            continue
        if c == "|":
            return beg, i + 1
        i += 1
    return beg, i


class Rules:
    # ----------------------------------------------------------- files db
    def lookup_file(self, name):
        return self.files.get(name)

    def enter_file(self, name):
        f = self.files.get(name)
        if f is not None and f.double_colon is None:
            return f
        new = File(name)
        if self.all_secondary:
            new.intermediate = True
        if f is None:
            self.files[name] = new
            self.file_order.append(new)
        else:
            new.double_colon = f
            while f.prev is not None:
                f = f.prev
            f.prev = new
        return new

    def parse_file_seq(self, s, stop="", strip=True):
        """Split a file list, honouring backslash quoting.

        Returns names; when stop is given, stops at it and records the rest in
        self.pfs_rest.
        """
        names = []
        i = 0
        while True:
            i = next_token(s, i)
            if i >= len(s) or (stop and s[i] == stop):
                break
            q = i
            s, p = find_char_unquote(s, stop, blank=True, start=q)
            if p < 0:
                p = len(s)
            if strip:
                while p - q > 2 and s[q] == "." and s[q + 1] == "/":
                    q += 2
                    while q < p and s[q] == "/":
                        q += 1
            names.append("./" if q == p else s[q:p])
            i = p
        self.pfs_rest = s[i:]
        return names

    def multi_glob(self, names):
        out = []
        for n in names:
            out.extend(self.glob_name(n))
        return out

    def parse_prereqs(self, text):
        names = self.multi_glob(self.parse_file_seq(text, "|", True))
        rest = self.pfs_rest
        deps = [Dep(name=n) for n in names]
        if rest:
            oo = self.multi_glob(self.parse_file_seq(rest[1:], "", True))
            deps.extend(Dep(name=n, ignore_mtime=True) for n in oo)
        return deps

    # ------------------------------------------------------ record rules
    def record_files(self, filenames, pattern, deps_text, cmds, two_colon, loc):
        implicit = False
        targets = []
        pattern_pct = -1
        if pattern is not None:
            pattern, pattern_pct = find_percent(pattern)
        for idx, name in enumerate(filenames):
            last = idx == len(filenames) - 1
            if name == ".SECONDEXPANSION":
                self.second_expansion = True
            name2, pct = find_percent(name)
            if pct >= 0:
                implicit = True
            if implicit and pattern is not None:
                self.fatal(loc, "mixed implicit and static pattern rules")
            if implicit and pct < 0:
                self.fatal(loc, "mixed implicit and normal rules")
            if implicit:
                targets.append(name2)
                continue
            this = None
            if pattern is not None and not pattern_matches(pattern, pattern_pct, name):
                continue
            elif deps_text is not None:
                this = DepLine(deps_text)
            if not two_colon:
                f = self.enter_file(name)
                if f.double_colon is not None:
                    self.fatal(loc, "target file '%s' has both : and :: entries" % f.name)
                if cmds is not None and cmds is f.cmds:
                    self.error(loc, "target '%s' given more than once in the same rule." % f.name)
                elif cmds is not None and f.cmds is not None and f.is_target:
                    self.error(cmds.loc, "warning: overriding commands for target '%s'" % f.name)
                f.is_target = True
                if f.name == ".DEFAULT" and this is None and cmds is None:
                    f.cmds = None
                if cmds is not None:
                    f.cmds = cmds
                if f.name == ".SUFFIXES" and this is None:
                    f.deplines = []
                    f.deps = []
                    f.last_dep_has_cmds = False
                elif this is not None:
                    if f.deplines:
                        if cmds is not None:
                            f.deplines.append(this)
                            f.last_dep_has_cmds = True
                        elif f.cmds is not None:
                            f.deplines.insert(len(f.deplines) - 1, this)
                        else:
                            f.deplines.append(this)
                    else:
                        f.deplines = [this]
                        if cmds is not None:
                            f.last_dep_has_cmds = True
            else:
                f = self.lookup_file(name)
                if f is not None and f.is_target and f.double_colon is None:
                    self.fatal(loc, "target file '%s' has both : and :: entries" % f.name)
                f = self.enter_file(name)
                if f.double_colon is None:
                    f.double_colon = f
                f.is_target = True
                f.deplines = [this] if this is not None else []
                f.cmds = cmds
            if pattern is not None:
                stem = patsubst_expand(name, pattern, "%", pattern_pct, 0)
                f.stem = stem
                if this is not None:
                    this.staticpattern = True
                    this.stem = stem
        if implicit:
            self.create_pattern_rule(targets, deps_text, cmds, two_colon, True)

    def create_pattern_rule(self, targets, deps_text, cmds, terminal, override):
        r = PatternRule(targets, deps_text, cmds, terminal)
        rules = self.pattern_rules
        for k, old in enumerate(rules):
            matched = False
            for t in r.targets:
                if all(ot == t for ot in old.targets):
                    matched = True
                    break
            if matched and (old.deps_text or "") == (r.deps_text or ""):
                if override:
                    del rules[k]
                    rules.append(r)
                    return
                return
        rules.append(r)

    def record_target_var(self, filenames, defn, origin, exported, loc):
        for name in filenames:
            name2, pct = find_percent(name)
            if pct >= 0:
                pv = self.parse_variable_definition(defn, loc)
                if pv is None:
                    self.error(loc, "Malformed target-specific variable definition")
                    continue
                vname, value, flavor = pv
                if flavor == "simple":
                    value = self.expand(value)
                p = PatternVar(name2, pct, vname, value, flavor, origin, loc,
                               exported)
                self.pattern_vars.append(p)
                continue
            f = self.enter_file(name)
            self.initialize_file_variables(f, reading=True)
            saved = self.setlist
            self.setlist = self.file_setlist(f)
            try:
                v = self.try_variable_definition(loc, defn, origin, True)
            finally:
                self.setlist = saved
            if v is None:
                self.error(loc, "Malformed target-specific variable definition")
                continue
            v.origin = origin
            v.per_target = True
            if exported:
                v.export = 1

    # ---------------------------------------------------------- snap deps
    def expand_deps(self, f):
        lines = f.deplines
        f.deplines = []
        new_deps = []
        for k, dl in enumerate(lines):
            deps = self.parse_prereqs(dl.text)
            if deps and dl.staticpattern:
                kept = []
                for d in deps:
                    nm, pct = find_percent(d.name)
                    d.name = nm
                    if pct >= 0:
                        if f.stem == "":
                            d.name = nm[:pct] + nm[pct + 1:]
                        else:
                            d.name = patsubst_expand(f.stem, "%", nm, 0, pct)
                        if d.name == "":
                            continue
                    kept.append(d)
                deps = kept
            for d in deps:
                d.file = self.lookup_file(d.name) or self.enter_file(d.name)
                d.name = None
            if k == len(lines) - 1 and f.last_dep_has_cmds:
                new_deps = deps + new_deps
            else:
                new_deps.extend(deps)
        f.deps = new_deps

    def snap_deps(self):
        for f in list(self.file_order):
            g = f
            while g is not None:
                self.expand_deps(g)
                g = g.prev

        def chain(name):
            f = self.lookup_file(name)
            out = []
            while f is not None:
                out.append(f)
                f = f.prev
            return out

        def dep_files(f):
            res = []
            for d in f.deps:
                g = d.file
                while g is not None:
                    res.append(g)
                    g = g.prev
            return res

        for f in chain(".PRECIOUS"):
            for g in dep_files(f):
                g.precious = True
        for f in chain(".PHONY"):
            for g in dep_files(f):
                g.phony = True
                g.is_target = True
                g.last_mtime = NONEXISTENT
                g.mtime_before_update = NONEXISTENT
        for f in chain(".INTERMEDIATE"):
            for g in dep_files(f):
                g.intermediate = True
        for f in chain(".SECONDARY"):
            if f.deps:
                for g in dep_files(f):
                    g.intermediate = True
                    g.secondary = True
            else:
                self.all_secondary = True
                for g in self.file_order:
                    g.intermediate = True
        f = self.lookup_file(".EXPORT_ALL_VARIABLES")
        if f is not None and f.is_target:
            self.export_all = True
        f = self.lookup_file(".IGNORE")
        if f is not None and f.is_target:
            if not f.deps:
                self.ignore_errors = True
            else:
                for g in dep_files(f):
                    g.command_flags |= COMMANDS_NOERROR
        f = self.lookup_file(".SILENT")
        if f is not None and f.is_target:
            if not f.deps:
                self.silent = True
            else:
                for g in dep_files(f):
                    g.command_flags |= COMMANDS_SILENT

    def convert_to_pattern(self):
        sf = self.lookup_file(".SUFFIXES")
        sufs = [d.file.name for d in sf.deps] if sf is not None else []
        for s in sufs:
            self.create_pattern_rule(["%" + s], None, None, False, False)
            f = self.lookup_file(s)
            if f is not None and f.cmds is not None:
                self.create_pattern_rule(["%"], "%" + s, f.cmds, False, False)
            for s2 in sufs:
                if s2 == s:
                    continue
                f = self.lookup_file(s + s2)
                if f is not None and f.cmds is not None:
                    self.create_pattern_rule(["%" + s2], "%" + s, f.cmds,
                                             False, False)

    # ------------------------------------------------------ file variables
    def file_setlist(self, f):
        """The variable set list of f: own, pattern, parent chain, global."""
        out = []
        seen = set()
        g = f
        while g is not None:
            if g.double_colon is not None and g.double_colon is not g:
                if g.variables is not None:
                    out.append(g.variables)
                g = g.double_colon
                continue
            if id(g) in seen:
                break
            seen.add(id(g))
            if g.variables is not None:
                out.append(g.variables)
            if g.pat_variables is not None:
                out.append(g.pat_variables)
            break
        out.append(self.globals)
        return out

    def initialize_file_variables(self, f, reading=False):
        if f.variables is None:
            f.variables = VarSet()
        if f.double_colon is not None and f.double_colon is not f:
            self.initialize_file_variables(f.double_colon, reading)
            return
        if f.parent is not None:
            self.initialize_file_variables(f.parent, reading)
        if not reading and not f.pat_searched:
            matches = [p for p in self.pattern_vars
                       if pattern_matches(p.target, p.percent, f.name)]
            if matches:
                pset = VarSet()
                f.pat_variables = pset
                saved = self.setlist
                self.setlist = [pset] + self.file_setlist_after_pattern(f)
                try:
                    for p in matches:
                        if p.flavor == "simple":
                            v = pset.define(p.name, p.value, p.origin, False,
                                            p.loc)
                        else:
                            v = self.do_variable_definition(
                                p.loc, p.name, p.value, p.origin, p.flavor,
                                True)
                        v.per_target = True
                        if p.exported:
                            v.export = 1
                finally:
                    self.setlist = saved
            f.pat_searched = True

    def file_setlist_after_pattern(self, f):
        sl = self.file_setlist(f)
        # drop f's own set (first entry) - lookups during pattern-variable
        # setup see the parent chain and the global set.
        if f.variables is not None and sl and sl[0] is f.variables:
            sl = sl[1:]
        return sl

    def set_file_variables(self, f):
        vs = f.variables
        name = f.name
        if f.stem is None:
            sf = self.enter_file(".SUFFIXES")
            stem = ""
            for d in sf.deps:
                s = d.file.name
                if len(name) > len(s) and name.endswith(s):
                    stem = name[:len(name) - len(s)]
                    break
            f.stem = stem
        default = self.lookup_file(".DEFAULT")
        if default is not None and f.cmds is not None and f.cmds is default.cmds:
            less = f.name
        else:
            less = f.deps[0].dep_name() if f.deps else ""
        vs.define("@", name, O_AUTOMATIC, False, None)
        vs.define("%", "", O_AUTOMATIC, False, None)
        vs.define("*", f.stem, O_AUTOMATIC, False, None)
        vs.define("<", less, O_AUTOMATIC, False, None)
        seen = set()
        uniq = []
        for d in f.deps:
            key = d.dep_name()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(d)
        f.deps = uniq
        plus = [d.dep_name() for d in f.deps if not d.ignore_mtime]
        vs.define("+", " ".join(plus), O_AUTOMATIC, False, None)
        caret = []
        qmark = []
        bar = []
        for d in uniq:
            n = d.dep_name()
            if d.ignore_mtime:
                bar.append(n)
            else:
                caret.append(n)
                if d.changed:
                    qmark.append(n)
        vs.define("^", " ".join(caret), O_AUTOMATIC, False, None)
        vs.define("?", " ".join(qmark), O_AUTOMATIC, False, None)
        vs.define("|", " ".join(bar), O_AUTOMATIC, False, None)

    # ------------------------------------------------------ implicit search
    def file_exists(self, name):
        return os.path.exists(name)

    def try_implicit_rule(self, f, depth):
        return self.pattern_search(f, depth, 0)

    def pattern_search(self, f, depth, recursions):
        filename = f.name
        namelen = len(filename)
        lastslash = filename.rfind("/")
        if lastslash >= 0 and lastslash == namelen - 1:
            lastslash = -1
        tryrules = []
        specific_rule_matched = False
        for rule in self.pattern_rules:
            if rule.deps_text is not None and rule.cmds is None:
                continue
            if rule.in_use:
                continue
            for ti, target in enumerate(rule.targets):
                pct = rule.percents[ti]
                if recursions > 0 and target == "%" and not rule.terminal:
                    continue
                if len(target) > namelen:
                    continue
                pre = target[:pct]
                suf = target[pct + 1:]
                check_lastslash = lastslash >= 0 and "/" not in target
                if check_lastslash:
                    base = filename[lastslash + 1:]
                else:
                    base = filename
                if len(pre) + len(suf) > len(base):
                    continue
                if not base.startswith(pre) or not base.endswith(suf):
                    continue
                if target != "%":
                    specific_rule_matched = True
                if rule.deps_text is None and rule.cmds is None:
                    continue
                stem = base[len(pre):len(base) - len(suf)]
                tryrules.append([rule, ti, check_lastslash, stem])
        if specific_rule_matched:
            for tr in tryrules:
                r = tr[0]
                if r is not None and not r.terminal and "%" in r.targets:
                    tr[0] = None
        # prefer the most specific rule: the one with the shortest stem
        tryrules.sort(key=lambda tr: len(tr[3]))
        self.initialize_file_variables(f)
        found = None
        found_deps = None
        for intermed_ok in (False, True):
            for tr in tryrules:
                rule, ti, check_lastslash, stem = tr
                if rule is None:
                    continue
                if intermed_ok and rule.terminal:
                    continue
                rule.in_use = True
                deps = []   # [name, ignore_mtime, intermediate_file_or_None, pattern]
                failed = False
                try:
                    text = rule.deps_text or ""
                    pos = 0
                    order_only = False
                    while True:
                        w = get_next_word(text, pos)
                        if w is None:
                            break
                        b, e = w
                        word = text[b:e]
                        pos = e
                        k = word.find("%")
                        add_dir = False
                        if k >= 0:
                            word = word[:k] + stem + word[k + 1:]
                            add_dir = check_lastslash
                        rest = word
                        while True:
                            names = self.multi_glob(self.parse_file_seq(
                                rest, "" if order_only else "|", True))
                            rem = self.pfs_rest
                            for nm in names:
                                if add_dir:
                                    nm = filename[:lastslash + 1] + nm
                                deps.append([nm, order_only, None, None])
                            if not order_only and rem:
                                rest = rem[1:]
                                order_only = True
                                continue
                            break
                    for d in deps:
                        name = d[0]
                        if name in self.impossible:
                            tr[0] = None
                            failed = True
                            break
                        if any(x.dep_name() == name for x in f.deps):
                            continue
                        if self.lookup_file(name) is not None or self.file_exists(name):
                            continue
                        if self.vpath_search(name) is not None:
                            continue
                        if intermed_ok:
                            imf = File(name)
                            if self.pattern_search(imf, depth + 1, recursions + 1):
                                d[2] = imf
                                d[3] = imf.name
                                imf.name = name
                                continue
                            self.impossible.add(name)
                        failed = True
                        break
                finally:
                    rule.in_use = False
                if not failed:
                    found = tr
                    found_deps = deps
                    break
            if found is not None:
                break
        if found is None:
            return False
        rule, ti, check_lastslash, stem = found
        if recursions > 0:
            f.name = rule.targets[ti]
        new_deps = []
        for name, oo, imf, ipat in found_deps:
            if imf is not None:
                g = self.lookup_file(imf.name)
                if g is not None:
                    g.precious = True
                else:
                    g = self.enter_file(imf.name)
                g.deps = imf.deps
                for dd in g.deps:
                    if dd.file is None:
                        dd.file = self.lookup_file(dd.name) or self.enter_file(dd.name)
                        if dd.changed:
                            dd.file.tried_implicit = True
                        dd.name = None
                g.cmds = imf.cmds
                g.stem = imf.stem
                g.also_make = imf.also_make
                g.is_target = True
                if not g.precious:
                    pf = self.lookup_file(ipat)
                    if pf is not None and pf.precious:
                        g.precious = True
                g.intermediate = True
                g.tried_implicit = True
            dep = Dep(ignore_mtime=oo)
            if recursions == 0:
                dep.file = self.lookup_file(name) or self.enter_file(name)
            else:
                dep.name = name
            if imf is None and rule.terminal:
                if dep.file is None:
                    dep.changed = True
                else:
                    dep.file.tried_implicit = True
            new_deps.append(dep)
        f.deps = new_deps + f.deps
        if not check_lastslash:
            f.stem = stem
        else:
            f.stem = filename[:lastslash + 1] + stem
        f.cmds = rule.cmds
        f.is_target = True
        pf = self.lookup_file(rule.targets[ti])
        if pf is not None and pf.precious:
            f.precious = True
        if len(rule.targets) > 1:
            also = []
            for i, t in enumerate(rule.targets):
                if i == ti:
                    continue
                pct = rule.percents[i]
                nm = t[:pct] + f.stem + t[pct + 1:]
                g = self.enter_file(nm)
                pf = self.lookup_file(t)
                if pf is not None and pf.precious:
                    g.precious = True
                g.is_target = True
                also.insert(0, g)
            f.also_make = also + f.also_make
        return True

    # --------------------------------------------------------------- vpath
    def vpath_search(self, name):
        if name.startswith("/") or (not self.vpaths and not self.general_vpath):
            return None
        for pat, pct, dirs in self.vpaths:
            if pattern_matches(pat, pct, name):
                r = self.selective_vpath_search(dirs, name)
                if r is not None:
                    return r
        if self.general_vpath:
            return self.selective_vpath_search(self.general_vpath, name)
        return None

    def selective_vpath_search(self, dirs, name):
        for d in dirs:
            cand = d + "/" + name if not d.endswith("/") else d + name
            if os.path.exists(cand):
                return cand
        return None

    def construct_vpath_list(self, pattern, dirpath):
        if dirpath is None:
            if pattern is None:
                self.vpaths_pending = []
            else:
                self.vpaths_pending = [v for v in self.vpaths_pending
                                       if v[0] != pattern]
            return None
        dirs = []
        i = 0
        n = len(dirpath)
        while i < n and (dirpath[i] == ":" or dirpath[i] in BLANKS):
            i += 1
        while i < n:
            j = i
            while j < n and dirpath[j] != ":" and dirpath[j] not in BLANKS:
                j += 1
            d = dirpath[i:j]
            if len(d) > 1 and d.endswith("/"):
                d = d[:-1]
            if len(d) > 1 or d != ".":
                if os.path.isdir(d):
                    dirs.append(d)
            i = j
            while i < n and (dirpath[i] == ":" or dirpath[i] in BLANKS):
                i += 1
        if not dirs:
            return None
        pat, pct = find_percent(pattern)
        return (pat, pct, dirs)
