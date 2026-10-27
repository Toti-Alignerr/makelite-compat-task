"""Variable expansion and the built-in functions."""

import glob as _glob
import os
import subprocess

from .text import (BLANKS, SPACES, words, space_words, word_spans, strip_space,
                   find_percent, pattern_matches, subst_expand,
                   patsubst_expand, next_token)
from .variables import (VarSet, O_AUTOMATIC, O_FILE, ORIGIN_NAMES)

# name: (min_args, max_args, expand_args)
FUNCTIONS = {
    "abspath": (0, 1, True),
    "addprefix": (2, 2, True),
    "addsuffix": (2, 2, True),
    "basename": (0, 1, True),
    "dir": (0, 1, True),
    "notdir": (0, 1, True),
    "subst": (3, 3, True),
    "suffix": (0, 1, True),
    "filter": (2, 2, True),
    "filter-out": (2, 2, True),
    "findstring": (2, 2, True),
    "firstword": (0, 1, True),
    "flavor": (0, 1, True),
    "join": (2, 2, True),
    "lastword": (0, 1, True),
    "patsubst": (3, 3, True),
    "realpath": (0, 1, True),
    "shell": (0, 1, True),
    "sort": (0, 1, True),
    "strip": (0, 1, True),
    "wildcard": (0, 1, True),
    "word": (2, 2, True),
    "wordlist": (3, 3, True),
    "words": (0, 1, True),
    "origin": (0, 1, True),
    "foreach": (3, 3, False),
    "call": (1, 0, True),
    "info": (0, 1, True),
    "error": (0, 1, True),
    "warning": (0, 1, True),
    "if": (2, 3, False),
    "or": (1, 0, False),
    "and": (1, 0, False),
    "value": (0, 1, True),
    "eval": (0, 1, True),
}

GLOB_CHARS = "*?["


def lookup_function(s, i):
    """Return (name, end) when s[i:] starts with a function name."""
    j = i
    n = len(s)
    while j < n and ("a" <= s[j] <= "z" or s[j] == "-"):
        j += 1
    if j >= n or s[j] in BLANKS:
        name = s[i:j]
        if name in FUNCTIONS:
            return name, j
    return None, j


def find_next_argument(openp, closep, s, i, end):
    count = 0
    while i < end:
        c = s[i]
        if c == openp:
            count += 1
        elif c == closep:
            count -= 1
            if count < 0:
                return -1
        elif c == "," and count == 0:
            return i
        i += 1
    return -1


def check_numeric(make, s, msg):
    t = strip_space(s)
    if not t or not t.isdigit() or not t.isascii():
        make.fatal(make.exp_loc(), "%s: '%s'" % (msg, s))


def c_atoi(s):
    s = s.lstrip(SPACES)
    sign = 1
    if s[:1] in ("-", "+"):
        if s[0] == "-":
            sign = -1
        s = s[1:]
    n = 0
    for c in s:
        if "0" <= c <= "9":
            n = n * 10 + (ord(c) - 48)
        else:
            break
    return sign * n


class Expander:
    # ----------------------------------------------------------- lookups
    def lookup(self, name):
        for vs in self.setlist:
            v = vs.get(name)
            if v is not None:
                return v
        return None

    def exp_loc(self):
        if self.exp_loc_stack:
            return self.exp_loc_stack[-1]
        return self.reading_loc

    # -------------------------------------------------------- expansion
    def expand(self, s):
        if "$" not in s:
            return s
        out = []
        i = 0
        n = len(s)
        while True:
            j = s.find("$", i)
            if j < 0:
                out.append(s[i:])
                break
            out.append(s[i:j])
            p = j + 1
            if p >= n:
                break
            c = s[p]
            if c == "$":
                out.append("$")
                i = p + 1
            elif c in "({":
                i = self._expand_paren(s, p, out)
                if i < 0:
                    break
            else:
                out.append(self.reference_variable(c))
                i = p + 1
        return "".join(out)

    def _expand_paren(self, s, p, out):
        """Handle $( or ${ at s[p]; return index to continue at, -1 for end."""
        openp = s[p]
        closep = ")" if openp == "(" else "}"
        beg = p + 1
        n = len(s)
        fname, fend = lookup_function(s, beg)
        if fname is not None:
            return self._call_function(s, fname, fend, openp, closep, out)
        end = s.find(closep, beg)
        if end < 0:
            self.fatal(self.exp_loc(), "unterminated variable reference")
        if "$" in s[beg:end]:
            count = 0
            q = beg
            while q < n:
                if s[q] == openp:
                    count += 1
                elif s[q] == closep:
                    count -= 1
                    if count < 0:
                        break
                q += 1
            if count < 0:
                name = self.expand(s[beg:q])
                nxt = q + 1
            else:
                name = s[beg:end]
                nxt = -1
        else:
            name = s[beg:end]
            nxt = end + 1
        colon = name.find(":")
        if colon >= 0:
            eq = name.find("=", colon + 1)
            if eq >= 0:
                vname = name[:colon]
                v = self.lookup(vname)
                if v is not None and v.value != "":
                    value = self.recursively_expand(v) if v.recursive else v.value
                    pat = "%" + name[colon + 1:eq]
                    rep = "%" + name[eq + 1:]
                    pat2, pp = find_percent(pat[1:])
                    if pp >= 0:
                        rep2, rp = find_percent(rep[1:])
                        out.append(patsubst_expand(value, pat2, rep2, pp, rp))
                    else:
                        out.append(patsubst_expand(value, "%" + pat2, rep, 0, 0))
                return nxt
        out.append(self.reference_variable(name))
        return nxt

    def _call_function(self, s, fname, fend, openp, closep, out):
        mn, mx, expand_args = FUNCTIONS[fname]
        beg = next_token(s, fend)
        n = len(s)
        count = 0
        end = beg
        while end < n:
            c = s[end]
            if c == openp:
                count += 1
            elif c == closep:
                count -= 1
                if count < 0:
                    break
            end += 1
        if count >= 0:
            self.fatal(self.exp_loc(),
                       "unterminated call to function '%s': missing '%s'"
                       % (fname, closep))
        args = []
        p = beg
        while p <= end:
            if len(args) + 1 == mx:
                nxt = end
            else:
                nxt = find_next_argument(openp, closep, s, p, end)
                if nxt < 0:
                    nxt = end
            raw = s[p:nxt]
            args.append(self.expand(raw) if expand_args else raw)
            p = nxt + 1
        out.append(self.builtin(fname, args))
        return end + 1

    def builtin(self, fname, args):
        mn = FUNCTIONS[fname][0]
        if len(args) < mn:
            self.fatal(self.exp_loc(),
                       "insufficient number of arguments (%d) to function '%s'"
                       % (len(args), fname))
        return getattr(self, "f_" + fname.replace("-", "_"))(args)

    def reference_variable(self, name):
        v = self.lookup(name)
        if v is None or (v.value == "" and not v.append):
            return ""
        if v.recursive:
            return self.recursively_expand(v)
        return v.value

    def recursively_expand(self, v, setlist=None):
        pushed = False
        if v.loc is not None:
            self.exp_loc_stack.append(v.loc)
            pushed = True
        set_reading = False
        if self.reading_loc is None and v.loc is not None:
            self.reading_loc = v.loc
            set_reading = True
        try:
            if v.expanding:
                self.fatal(self.exp_loc(),
                           "Recursive variable '%s' references itself (eventually)"
                           % v.name)
            saved = None
            if setlist is not None:
                saved = self.setlist
                self.setlist = setlist
            v.expanding = True
            try:
                if v.append:
                    value = self.variable_append(v.name, 0)
                else:
                    value = self.expand(v.value)
            finally:
                v.expanding = False
                if saved is not None:
                    self.setlist = saved
            return value
        finally:
            if pushed:
                self.exp_loc_stack.pop()
            if set_reading:
                self.reading_loc = None

    def variable_append(self, name, idx):
        sl = self.setlist
        while idx < len(sl):
            v = sl[idx].get(name)
            if v is not None:
                break
            idx += 1
        else:
            return ""
        buf = self.variable_append(name, idx + 1) if v.append else ""
        if buf:
            buf += " "
        if not v.recursive:
            return buf + v.value
        return buf + self.expand(v.value)

    # ------------------------------------------------------- functions
    def f_subst(self, a):
        return subst_expand(a[2], a[0], a[1], False)

    def f_patsubst(self, a):
        return patsubst_expand(a[2], a[0], a[1])

    def f_strip(self, a):
        return " ".join(space_words(a[0]))

    def f_findstring(self, a):
        return a[0] if a[0] in a[1] else ""

    def _filter(self, a, keep):
        pats = []
        for w in words(a[0]):
            w2, pc = find_percent(w)
            pats.append((w2, pc))
        out = []
        for w in words(a[1]):
            m = any(pattern_matches(p, pc, w) for p, pc in pats)
            if m == keep:
                out.append(w)
        return " ".join(out)

    def f_filter(self, a):
        return self._filter(a, True)

    def f_filter_out(self, a):
        return self._filter(a, False)

    def f_sort(self, a):
        ws = sorted(set(words(a[0])), key=lambda w: w.encode("latin-1"))
        return " ".join(ws)

    def f_word(self, a):
        check_numeric(self, a[0], "non-numeric first argument to 'word' function")
        i = c_atoi(a[0])
        if i == 0:
            self.fatal(self.exp_loc(),
                       "first argument to 'word' function must be greater than 0")
        ws = words(a[1])
        return ws[i - 1] if i <= len(ws) else ""

    def f_wordlist(self, a):
        check_numeric(self, a[0],
                      "non-numeric first argument to 'wordlist' function")
        check_numeric(self, a[1],
                      "non-numeric second argument to 'wordlist' function")
        start = c_atoi(a[0])
        if start < 1:
            self.fatal(self.exp_loc(),
                       "invalid first argument to 'wordlist' function: '%d'" % start)
        count = c_atoi(a[1]) - start + 1
        if count <= 0:
            return ""
        spans = word_spans(a[2])
        if start > len(spans):
            return ""
        last = min(len(spans), start + count - 1)
        return a[2][spans[start - 1][0]:spans[last - 1][1]]

    def f_words(self, a):
        return str(len(words(a[0])))

    def f_firstword(self, a):
        ws = words(a[0])
        return ws[0] if ws else ""

    def f_lastword(self, a):
        ws = words(a[0])
        return ws[-1] if ws else ""

    def f_dir(self, a):
        out = []
        for w in words(a[0]):
            d = os.path.dirname(w)
            out.append(d + "/" if d else "./")
        return " ".join(out)

    def f_notdir(self, a):
        out = []
        for w in words(a[0]):
            k = w.rfind("/")
            out.append(w[k + 1:])
        return " ".join(out)

    def f_suffix(self, a):
        out = []
        for w in words(a[0]):
            ext = os.path.splitext(w)[1]
            if ext:
                out.append(ext)
        return " ".join(out)

    def f_basename(self, a):
        return " ".join(os.path.splitext(w)[0] for w in words(a[0]))

    def f_addprefix(self, a):
        return " ".join(a[0] + w for w in words(a[1]))

    def f_addsuffix(self, a):
        return " ".join(w + a[0] for w in words(a[1]))

    def f_join(self, a):
        l1, l2 = words(a[0]), words(a[1])
        out = []
        for i in range(max(len(l1), len(l2))):
            out.append((l1[i] if i < len(l1) else "") +
                       (l2[i] if i < len(l2) else ""))
        return " ".join(out)

    def f_wildcard(self, a):
        out = []
        for name in self.parse_file_seq(a[0], strip=False):
            for m in self.glob_name(name):
                if os.path.lexists(m) and os.path.exists(m):
                    out.append(m)
        return " ".join(out)

    def glob_name(self, name):
        if not any(c in name for c in GLOB_CHARS):
            return [name]
        res = _glob.glob(name)
        if not res:
            return [name]
        return sorted(res, key=lambda w: w.encode("latin-1"))

    def f_realpath(self, a):
        out = []
        for w in words(a[0]):
            if os.path.exists(w):
                out.append(os.path.realpath(w))
        return " ".join(out)

    def f_abspath(self, a):
        return " ".join(os.path.normpath(os.path.join(os.getcwd(), w))
                        for w in words(a[0]))

    def f_origin(self, a):
        v = self.lookup(a[0])
        if v is None:
            return "undefined"
        return ORIGIN_NAMES[v.origin]

    def f_flavor(self, a):
        v = self.lookup(a[0])
        if v is None:
            return "undefined"
        return "recursive" if v.recursive else "simple"

    def f_value(self, a):
        v = self.lookup(a[0])
        return v.value if v is not None else ""

    def f_foreach(self, a):
        varname = self.expand(a[0])
        lst = self.expand(a[1])
        body = a[2]
        scope = VarSet()
        self.setlist.insert(0, scope)
        try:
            v = scope.define(varname, "", O_AUTOMATIC, False, None)
            out = []
            for w in words(lst):
                v.value = w
                out.append(self.expand(body))
            return " ".join(out)
        finally:
            self.setlist.pop(0)

    def f_if(self, a):
        cond = a[0]
        result = bool(cond) and len(self.expand(cond)) > 0
        if result:
            return self.expand(a[1])
        if len(a) > 2:
            return self.expand(a[2])
        return ""

    def f_or(self, a):
        for arg in a:
            t = strip_space(arg)
            if not t:
                continue
            e = self.expand(t)
            if e:
                return e
        return ""

    def f_and(self, a):
        e = ""
        for arg in a:
            t = strip_space(arg)
            if not t:
                return ""
            e = self.expand(t)
            if not e:
                return ""
        return e

    def f_call(self, a):
        fname = strip_space(a[0])
        if not fname:
            return ""
        if fname in FUNCTIONS:
            return self.builtin(fname, a[1:])
        v = self.lookup(fname)
        if v is None or v.value == "":
            return ""
        scope = VarSet()
        args = a[1:]
        for i, val in enumerate(args, 1):
            scope.define(str(i), val, O_AUTOMATIC, False, None)
        for i in range(len(args), self.call_max_args):
            scope.define(str(i), "", O_AUTOMATIC, False, None)
        saved = self.call_max_args
        self.call_max_args = len(args)
        self.setlist.insert(0, scope)
        was = v.expanding
        v.expanding = False
        try:
            return self.expand("$(" + fname + ")")
        finally:
            v.expanding = was
            self.setlist.pop(0)
            self.call_max_args = saved

    def f_info(self, a):
        self.out_write(a[0] + "\n")
        return ""

    def f_warning(self, a):
        self.error(self.reading_loc, a[0])
        return ""

    def f_error(self, a):
        self.fatal(self.reading_loc, a[0])

    def f_eval(self, a):
        self.eval_buffer(a[0])
        return ""

    def f_shell(self, a):
        self.flush_out()
        try:
            r = subprocess.run(["/bin/sh", "-c", a[0].encode("latin-1")],
                               stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=self.shell_env)
        except OSError:
            return ""
        if r.stderr:
            self.err_write(r.stderr.decode("latin-1"))
        text = r.stdout.decode("latin-1").replace("\r\n", "\n")
        return text.rstrip("\n")
