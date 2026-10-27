"""Reading makefiles: lines, directives, variable definitions and rules."""

import re

from .text import (BLANKS, SPACES, next_token, end_of_token, words,
                   find_char_unquote, strip_space)
from .variables import O_FILE, O_OVERRIDE, EXPORT_YES, EXPORT_NO
from .rules import Commands

W_EOL, W_STATIC, W_VARIABLE, W_COLON, W_DCOLON, W_SEMICOLON, W_VARASSIGN = range(7)


class LineReader:
    def __init__(self, text):
        self.lines = text.split("\n")
        if self.lines and self.lines[-1] == "" and text.endswith("\n"):
            self.lines.pop()
        if text == "":
            self.lines = []
        self.pos = 0

    def readline(self):
        """Return (logical_line, physical_line_count) or None at the end."""
        if self.pos >= len(self.lines):
            return None
        parts = []
        n = 0
        while self.pos < len(self.lines):
            ln = self.lines[self.pos]
            self.pos += 1
            n += 1
            if ln.endswith("\r"):
                ln = ln[:-1]
            k = len(ln) - 1
            bs = 0
            while k >= 0 and ln[k] == "\\":
                bs += 1
                k -= 1
            parts.append(ln)
            if bs % 2 == 0:
                break
        return "\n".join(parts), n


def collapse_continuations(line):
    i = line.find("\n")
    if i < 0:
        return line
    out = list(line[:i])
    while out and out[-1] == "\\":
        out.pop()
    n = len(line)
    while i < n:
        # i points at a newline
        backslash = False
        bs_write = 0
        p = i - 1
        while p >= 0 and line[p] == "\\":
            if backslash:
                bs_write += 1
            backslash = not backslash
            p -= 1
        out.extend("\\" * bs_write)
        i += 1
        if backslash:
            i = next_token(line, i)
            while out and out[-1] in BLANKS:
                out.pop()
            out.append(" ")
        else:
            out.append("\n")
        while i < n:
            c = line[i]
            if c == "\\":
                p = i + 1
                while p < n and line[p] == "\\":
                    p += 1
                if p < n and line[p] == "\n":
                    i = p
                    break
                out.extend(line[i:p])
                i = p
            else:
                out.append(c)
                i += 1
        else:
            break
    return "".join(out)


def remove_comments(line):
    line, k = find_char_unquote(line, "#")
    if k >= 0:
        return line[:k]
    return line


def get_next_mword(s, i, delim=None):
    """Return (type, start, end)."""
    n = len(s)
    while i < n and s[i] in BLANKS:
        i += 1
    beg = i
    if i >= n:
        return W_EOL, beg, beg
    c = s[i]
    p = i + 1
    nxt = s[p] if p < n else ""
    if c == ";":
        return W_SEMICOLON, beg, p
    if c == "=":
        return W_VARASSIGN, beg, p
    if c == ":":
        if nxt == ":":
            return W_DCOLON, beg, p + 1
        if nxt == "=":
            return W_VARASSIGN, beg, p + 1
        return W_COLON, beg, p
    if c in "+?" and nxt == "=":
        return W_VARASSIGN, beg, p + 1
    if delim and c in delim:
        return W_STATIC, beg, p
    wtype = W_STATIC
    i = beg
    while i < n:
        c = s[i]
        if c in " \t=:":
            break
        if c == "$":
            i += 1
            if i >= n:
                break
            c2 = s[i]
            if c2 == "$":
                i += 1
                continue
            wtype = W_VARIABLE
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
        if c in "?+":
            if i + 1 < n and s[i + 1] == "=":
                break
            i += 1
            continue
        if c == "\\":
            if i + 1 < n and s[i + 1] in ":;=\\":
                i += 2
                continue
            i += 1
            continue
        if delim and c in delim:
            break
        i += 1
    return wtype, beg, i


class Conditionals:
    def __init__(self):
        self.ignoring = []
        self.seen_else = []


class Reader:
    # --------------------------------------------------------- variables
    def parse_variable_definition(self, line, loc):
        """Return (name, value, flavor) or None."""
        i = 0
        n = len(line)
        flavor = None
        while True:
            if i >= n:
                return None
            c = line[i]
            i += 1
            if c == "#":
                return None
            if c == "=":
                end = i - 1
                flavor = "recursive"
                break
            if c == ":":
                if i < n and line[i] == "=":
                    end = i - 1
                    i += 1
                    flavor = "simple"
                    break
                if line[i:i + 2] == ":=":
                    end = i - 1
                    i += 2
                    flavor = "simple"
                    break
                return None
            if c == "!" and i < n and line[i] == "=":
                end = i - 1
                i += 1
                flavor = "shell"
                break
            if c == "+" and i < n and line[i] == "=":
                end = i - 1
                i += 1
                flavor = "append"
                break
            if c == "?" and i < n and line[i] == "=":
                end = i - 1
                i += 1
                flavor = "conditional"
                break
            if c == "$":
                if i >= n:
                    continue
                c = line[i]
                i += 1
                if c == "(":
                    closep = ")"
                elif c == "{":
                    closep = "}"
                else:
                    continue
                count = 0
                while i < n:
                    if line[i] == c:
                        count += 1
                    elif line[i] == closep:
                        count -= 1
                        if count < 0:
                            i += 1
                            break
                    i += 1
        beg = next_token(line, 0)
        while end > beg and line[end - 1] in BLANKS:
            end -= 1
        value = line[next_token(line, i):]
        name = self.expand(line[beg:end])
        if name == "":
            self.fatal(loc, "empty variable name")
        return name, value, flavor

    def try_variable_definition(self, loc, line, origin, target_var):
        pv = self.parse_variable_definition(line, loc)
        if pv is None:
            return None
        name, value, flavor = pv
        return self.do_variable_definition(loc, name, value, origin, flavor,
                                           target_var)

    def do_variable_definition(self, loc, name, value, origin, flavor,
                               target_var):
        append = False
        conditional = False
        if flavor == "simple":
            p = self.expand(value)
            recursive = False
        elif flavor == "conditional":
            v = self.lookup(name)
            if v is not None:
                return v
            conditional = True
            p = value
            recursive = True
        elif flavor == "recursive":
            p = value
            recursive = True
        elif flavor == "shell":
            p = self.f_shell([self.expand(value)])
            recursive = True
        else:  # append
            if target_var:
                append = True
                v = self.setlist[0].get(name)
                if v is not None and not v.append:
                    append = False
            else:
                v = self.lookup(name)
            if v is None:
                p = value
                recursive = True
            else:
                # += keeps the origin of the value it extends
                origin = max(origin, v.origin)
                if v.recursive:
                    recursive = True
                    val = value
                else:
                    recursive = False
                    val = self.expand(value)
                p = v.value + " " + val
        target_set = self.setlist[0] if target_var else self.globals
        v = target_set.define(name, p, origin, recursive, loc,
                              self.env_overrides)
        v.append = append
        v.conditional = conditional
        return v

    def define_global(self, name, value, origin, recursive, loc):
        return self.globals.define(name, value, origin, recursive, loc,
                                   self.env_overrides)

    # ------------------------------------------------------- conditionals
    def conditional_line(self, line, wlen, loc):
        cmd = line[:wlen]
        if cmd not in ("ifdef", "ifndef", "ifeq", "ifneq", "else", "endif"):
            return -2
        c = self.conditionals
        rest = line[next_token(line, wlen):]
        if cmd == "endif":
            if rest:
                self.error(loc, "Extraneous text after 'endif' directive")
            if not c.ignoring:
                self.fatal(loc, "extraneous 'endif'")
            c.ignoring.pop()
            c.seen_else.pop()
            return 1 if any(c.ignoring) else 0
        if cmd == "else":
            if not c.ignoring:
                self.fatal(loc, "extraneous 'else'")
            o = len(c.ignoring) - 1
            if c.seen_else[o]:
                self.fatal(loc, "only one 'else' per conditional")
            if c.ignoring[o] == 0:
                c.ignoring[o] = 2
            elif c.ignoring[o] == 1:
                c.ignoring[o] = 0
            if not rest:
                c.seen_else[o] = True
                return 1 if any(c.ignoring) else 0
            k = 1
            while k < len(rest) and rest[k] not in SPACES:
                k += 1
            w = rest[:k]
            if w in ("else", "endif"):
                self.error(loc, "Extraneous text after 'else' directive")
            else:
                r = self.conditional_line(rest, k, loc)
                if r < 0:
                    self.error(loc, "Extraneous text after 'else' directive")
                else:
                    if c.ignoring[o] < 2:
                        c.ignoring[o] = c.ignoring[o + 1]
                    c.ignoring.pop()
                    c.seen_else.pop()
            return 1 if any(c.ignoring) else 0
        o = len(c.ignoring)
        c.ignoring.append(0)
        c.seen_else.append(False)
        if any(c.ignoring[:o]):
            c.ignoring[o] = 1
            return 1
        if cmd in ("ifdef", "ifndef"):
            var = self.expand(rest)
            e = end_of_token(var, 0)
            if next_token(var, e) < len(var):
                return -1
            v = self.lookup(var[:e])
            defined = v is not None and v.value != ""
            c.ignoring[o] = 1 if defined == (cmd == "ifndef") else 0
        else:
            s = rest
            if not s:
                return -1
            termin = "," if s[0] == "(" else s[0]
            if termin not in ",\"'":
                return -1
            i = 1
            if termin == ",":
                count = 0
                while i < len(s):
                    ch = s[i]
                    if ch == "(":
                        count += 1
                    elif ch == ")":
                        count -= 1
                    elif ch == "," and count <= 0:
                        break
                    i += 1
            else:
                while i < len(s) and s[i] != termin:
                    i += 1
            if i >= len(s):
                return -1
            if termin == ",":
                e1 = i
                while e1 > 1 and s[e1 - 1] in BLANKS:
                    e1 -= 1
                s1 = s[1:e1]
            else:
                s1 = s[1:i]
            i += 1
            s1 = self.expand(s1)
            if termin != ",":
                i = next_token(s, i)
            termin = ")" if termin == "," else (s[i] if i < len(s) else "")
            if termin not in (")", '"', "'"):
                return -1
            if termin == ")":
                count = 0
                b2 = next_token(s, i)
                i = b2
                while i < len(s):
                    ch = s[i]
                    if ch == "(":
                        count += 1
                    elif ch == ")":
                        if count <= 0:
                            break
                        count -= 1
                    i += 1
            else:
                i += 1
                b2 = i
                while i < len(s) and s[i] != termin:
                    i += 1
            if i >= len(s):
                return -1
            s2raw = s[b2:i]
            i = next_token(s, i + 1)
            if i < len(s):
                self.error(loc, "Extraneous text after '%s' directive" % cmd)
            s2 = self.expand(s2raw)
            c.ignoring[o] = 1 if (s1 == s2) == (cmd == "ifneq") else 0
        return 1 if any(c.ignoring) else 0

    # -------------------------------------------------------------- define
    def do_define(self, name, origin, reader, state):
        defstart = (state["filename"], state["lineno"])
        var = self.expand(name)
        flavor = "recursive"
        m = re.match(r"(.*?)\s*(:?=)$", var)
        if m:
            var = m.group(1)
            flavor = "simple" if m.group(2) == ":=" else "recursive"
        nlevels = 1
        body = []
        while True:
            r = reader.readline()
            if r is None:
                break
            line, nl = r
            state["lineno"] += state["nlines"]
            state["nlines"] = nl
            line = collapse_continuations(line)
            if not line.startswith("\t"):
                p = next_token(line, 0)
                t = line[p:]
                if t.startswith("define") and (len(t) == 6 or t[6] in BLANKS):
                    nlevels += 1
                elif t.startswith("endef") and (len(t) == 5 or t[5] in BLANKS):
                    after = remove_comments(t[5:])
                    if next_token(after, 0) < len(after):
                        self.error((state["filename"], state["lineno"]),
                                   "Extraneous text after 'endef' directive")
                    nlevels -= 1
                    if nlevels == 0:
                        value = "\n".join(body)
                        if flavor == "simple":
                            value = self.expand(value)
                        self.define_global(var, value, origin, flavor != "simple",
                                           defstart)
                        return
            body.append(line)
        self.fatal(defstart, "missing 'endef', unterminated 'define'")

    # ---------------------------------------------------------------- eval
    def eval_makefile(self, filename, dontcare=False):
        try:
            with open(filename, "rb") as fh:
                data = fh.read().decode("latin-1")
        except OSError:
            f = self.lookup_file(filename) or self.enter_file(filename)
            if dontcare:
                f.dontcare = True
            self.read_makefiles.append(f)
            return False
        f = self.lookup_file(filename) or self.enter_file(filename)
        if dontcare:
            f.dontcare = True
        self.read_makefiles.append(f)
        self.do_variable_definition((filename, 1), "MAKEFILE_LIST", filename,
                                    O_FILE, "append", False)
        saved_loc = self.reading_loc
        saved_cond = self.conditionals
        self.conditionals = Conditionals()
        try:
            self.eval(LineReader(data), filename, 1)
        finally:
            self.conditionals = saved_cond
            self.reading_loc = saved_loc
        return True

    def eval_buffer(self, text):
        loc = self.reading_loc
        if loc is None:
            loc = ("make", 0)
        saved_loc = self.reading_loc
        saved_cond = self.conditionals
        saved_setlist = self.setlist
        self.conditionals = Conditionals()
        try:
            self.eval(LineReader(text), loc[0], loc[1])
        finally:
            self.conditionals = saved_cond
            self.reading_loc = saved_loc
            self.setlist = saved_setlist

    def eval(self, reader, filename, first_lineno):
        st = {"filename": filename, "lineno": first_lineno, "nlines": 0}
        pend = {"filenames": None}
        no_targets = False
        in_ignored_define = False
        ignoring = 1 if any(self.conditionals.ignoring) else 0

        def record_waiting():
            if pend["filenames"] is not None:
                cl = pend["cmds"]
                cmds = None
                if cl:
                    cmds = Commands((filename, pend["cmds_started"]), cl)
                self.record_files(pend["filenames"], pend["pattern"],
                                  pend["deps"], cmds, pend["two_colon"],
                                  pend["loc"])
                pend["filenames"] = None

        while True:
            st["lineno"] += st["nlines"]
            r = reader.readline()
            if r is None:
                break
            line, st["nlines"] = r
            loc = (filename, st["lineno"])
            self.reading_loc = loc

            if line.startswith("\t"):
                if no_targets:
                    continue
                if pend["filenames"] is not None:
                    if ignoring:
                        continue
                    if not pend["cmds"]:
                        pend["cmds_started"] = st["lineno"]
                    pend["cmds"].append((line, st["lineno"]))
                    continue

            collapsed = remove_comments(collapse_continuations(line))
            p = 0
            while p < len(collapsed) and collapsed[p] in SPACES:
                p += 1
            if p >= len(collapsed):
                continue
            p2 = p + 1
            while p2 < len(collapsed) and collapsed[p2] not in SPACES:
                p2 += 1
            word = collapsed[p:p2]
            while p2 < len(collapsed) and collapsed[p2] in SPACES:
                p2 += 1
            rest = collapsed[p2:]

            skip_cond = (len(rest) == 1 and rest in ":+=")
            if skip_cond and ignoring:
                continue
            if not skip_cond:
                if not in_ignored_define:
                    i = self.conditional_line(collapsed[p:], len(word), loc)
                    if i != -2:
                        if i == -1:
                            self.fatal(loc, "invalid syntax in conditional")
                        ignoring = i
                        continue
                if word == "endef":
                    if not in_ignored_define:
                        self.fatal(loc, "extraneous 'endef'")
                    in_ignored_define = False
                    continue
                if word == "define":
                    if ignoring:
                        in_ignored_define = True
                    else:
                        if rest == "":
                            self.fatal(loc, "empty variable name")
                        name = rest.rstrip(BLANKS)
                        self.do_define(name, O_FILE, reader, st)
                        self.reading_loc = (filename, st["lineno"])
                    continue
                if word == "override":
                    if rest == "":
                        self.error(loc, "empty 'override' directive")
                    if rest.startswith("define") and (len(rest) == 6 or rest[6] in BLANKS):
                        if ignoring:
                            in_ignored_define = True
                        else:
                            r2 = rest[next_token(rest, 6):]
                            if r2 == "":
                                self.fatal(loc, "empty variable name")
                            self.do_define(r2.rstrip(BLANKS), O_OVERRIDE, reader, st)
                            self.reading_loc = (filename, st["lineno"])
                    elif not ignoring and self.try_variable_definition(
                            loc, rest, O_OVERRIDE, False) is None:
                        self.error(loc, "invalid 'override' directive")
                    continue
                if ignoring:
                    continue
                if word == "export":
                    if rest == "":
                        self.export_all = True
                    else:
                        v = self.try_variable_definition(loc, rest, O_FILE, False)
                        if v is not None:
                            v.export = EXPORT_YES
                        else:
                            for nm in words(self.expand(rest)):
                                v = self.lookup(nm)
                                if v is None:
                                    v = self.define_global(nm, "", O_FILE, False, loc)
                                v.export = EXPORT_YES
                    record_waiting()
                    continue
                if word == "unexport":
                    if rest == "":
                        self.export_all = False
                    else:
                        for nm in words(self.expand(rest)):
                            v = self.lookup(nm)
                            if v is None:
                                v = self.define_global(nm, "", O_FILE, False, loc)
                            v.export = EXPORT_NO
                    record_waiting()
                    continue
            # skip_conditionals:
            if word == "vpath":
                ex = self.expand(rest)
                ws = words(ex)
                if ws:
                    pat = ws[0]
                    k = ex.find(pat) + len(pat)
                    k = next_token(ex, k)
                    dirs = ex[k:] if k < len(ex) else None
                    if dirs is None:
                        self.construct_vpath_list(pat, None)
                    else:
                        entry = self.construct_vpath_list(pat, dirs)
                        if entry is not None:
                            self.vpaths_pending.append(entry)
                else:
                    self.construct_vpath_list(None, None)
                record_waiting()
                continue
            if word in ("include", "-include", "sinclude"):
                noerror = word[0] != "i"
                ex = self.expand(rest)
                if ex == "":
                    continue
                names = self.multi_glob(self.parse_file_seq(ex, "", True))
                saved = self.conditionals
                self.conditionals = Conditionals()
                record_waiting()
                try:
                    for nm in names:
                        ok = self.eval_makefile(nm, dontcare=noerror)
                        if not ok and not noerror:
                            self.error(loc, "%s: No such file or directory" % nm)
                            self.missing_include = (nm, loc)
                finally:
                    self.conditionals = saved
                    self.reading_loc = loc
                record_waiting()
                continue
            if self.try_variable_definition(loc, collapsed[p:], O_FILE, False) is not None:
                record_waiting()
                continue
            if line.startswith("\t"):
                self.fatal(loc, "recipe commences before first target")
            # ------------------------------------------------- rule line
            record_waiting()
            r = self.parse_rule_line(line, loc)
            if r == "no_targets":
                no_targets = True
                continue
            if r is None or r == "no_targets_tv":
                continue
            no_targets = False
            filenames, pattern, deps, cmd, two_colon = r
            pend.update(filenames=filenames, pattern=pattern, deps=deps,
                        two_colon=two_colon, loc=loc, cmds=[],
                        cmds_started=st["lineno"])
            if cmd is not None:
                pend["cmds"].append((cmd, st["lineno"]))
            self.maybe_set_default_goal(filenames)
        record_waiting()
        if self.conditionals.ignoring:
            self.fatal((filename, st["lineno"]), "missing 'endif'")

    def maybe_set_default_goal(self, filenames):
        dg = self.globals.get(".DEFAULT_GOAL")
        if dg is not None and dg.value != "":
            return
        sf = self.lookup_file(".SUFFIXES")
        sufs = [d.file.name if d.file is not None else d.name
                for d in (sf.deps if sf is not None else [])]
        sufs += [w for dl in (sf.deplines if sf is not None else [])
                 for w in words(dl.text)]
        for name in filenames:
            if "%" in name:
                break
            if name.startswith(".") and "/" not in name:
                continue
            reject = False
            for s in sufs:
                if not s.startswith(".") and name == s:
                    reject = True
                    break
                for s2 in sufs:
                    if name.startswith(s2) and name[len(s2):] == s:
                        reject = True
                        break
                if reject:
                    break
            if not reject:
                self.define_global(".DEFAULT_GOAL", name, O_FILE, False, None)
                break

    def parse_rule_line(self, line, loc):
        line, k = find_char_unquote(line, ";#", ignorevars=True)
        cmdleft = None
        if k >= 0 and line[k] == "#":
            line = line[:k]
        elif k >= 0:
            cmdleft = line[k + 1:]
            line = line[:k]
        semip = cmdleft
        line = collapse_continuations(line)
        wtype, b, e = get_next_mword(line, 0)
        if wtype == W_EOL:
            if cmdleft is not None:
                self.fatal(loc, "missing rule before commands")
            return None
        if wtype in (W_COLON, W_DCOLON):
            return "no_targets"
        buf = self.expand(line[b:e])
        lb = e
        colon = -1
        piece_start = 0
        while True:
            if cmdleft is None:
                buf2, sc = find_char_unquote(buf, ";", start=piece_start)
                buf = buf2
                if sc >= 0:
                    tail = buf[sc + 1:] + self.expand(line[lb:])
                    buf = buf[:sc]
                    lb = len(line)
                    cmdleft = tail
            buf2, colon = find_char_unquote(buf, ":", start=piece_start)
            buf = buf2
            if colon >= 0:
                break
            wtype, b, e = get_next_mword(line, lb)
            if wtype == W_EOL:
                break
            buf += " "
            piece_start = len(buf)
            buf += self.expand(line[b:e])
            lb = e
        if colon < 0:
            if buf[next_token(buf, 0):] != "":
                hint = (""
                        if line.startswith("        ") else "")
                self.fatal(loc, "missing separator" + hint)
            return None
        targets_text = buf[:colon]
        filenames = self.multi_glob(self.parse_file_seq(targets_text, "", True))
        if not filenames:
            return "no_targets"
        p2 = buf[colon + 1:]
        two_colon = p2.startswith(":")
        if two_colon:
            p2 = p2[1:]
        rest_raw = line[lb:]
        mixed = p2 + rest_raw
        wtype, b, e = get_next_mword(mixed, 0)
        origin = O_FILE
        exported = False
        if wtype == W_STATIC:
            w = mixed[b:e]
            if w == "override":
                origin = O_OVERRIDE
                wtype, b, e = get_next_mword(mixed, e)
            elif w == "export":
                exported = True
                wtype, b, e = get_next_mword(mixed, e)
        if wtype != W_EOL:
            wtype2, _, _ = get_next_mword(mixed, e)
        else:
            wtype2 = W_EOL
        if wtype2 == W_VARASSIGN:
            defn = mixed[b:]
            if semip is not None:
                defn = defn + ";" + semip
            self.record_target_var(filenames, defn, origin, exported, loc)
            return "no_targets_tv"
        rest_raw, _ = find_char_unquote(rest_raw, "=")
        deps_text = p2 + self.expand(rest_raw) if rest_raw else p2
        if cmdleft is None:
            deps_text, sc = find_char_unquote(deps_text, ";")
            if sc >= 0:
                cmdleft = deps_text[sc + 1:]
                deps_text = deps_text[:sc]
        pattern = None
        k = deps_text.find(":")
        while k > 0 and deps_text[k - 1] == "\\":
            q = k - 1
            bs = False
            while q >= 0 and deps_text[q] == "\\":
                bs = not bs
                q -= 1
            if bs:
                k = deps_text.find(":", k + 1)
            else:
                break
        if k >= 0:
            pats = self.parse_file_seq(deps_text, ":", True)
            rem = self.pfs_rest
            if not pats:
                self.fatal(loc, "missing target pattern")
            if len(pats) > 1:
                self.fatal(loc, "multiple target patterns")
            pattern = pats[0]
            if "%" not in pattern:
                self.fatal(loc, "target pattern contains no '%'")
            deps_text = rem[1:]
        d = strip_space(deps_text)
        deps = d if d else None
        return filenames, pattern, deps, cmdleft, two_colon
