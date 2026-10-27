"""Low-level text helpers that follow make's notion of words and quoting."""

BLANKS = " \t"
SPACES = " \t\n\v\f\r"


def next_token(s, i=0):
    n = len(s)
    while i < n and s[i] in BLANKS:
        i += 1
    return i


def end_of_token(s, i=0):
    n = len(s)
    while i < n and s[i] not in BLANKS:
        i += 1
    return i


def words(s):
    """Split a text into whitespace-separated words."""
    return s.split()


def word_spans(s):
    out = []
    i = 0
    n = len(s)
    while True:
        while i < n and s[i] in BLANKS:
            i += 1
        if i >= n:
            return out
        j = i
        while j < n and s[j] not in BLANKS:
            j += 1
        out.append((i, j))
        i = j


def strip_space(s):
    """strip_whitespace: trims isspace characters on both ends."""
    return s.strip(SPACES)


def space_words(s):
    """Split on any isspace character (used by strip)."""
    out = []
    cur = []
    for c in s:
        if c in SPACES:
            if cur:
                out.append("".join(cur))
                cur = []
        else:
            cur.append(c)
    if cur:
        out.append("".join(cur))
    return out


def find_char_unquote(s, stops, blank=False, ignorevars=False, start=0):
    """Find the first unquoted stop character.

    Backslashes before a stop character quote it; half of them are removed.
    Returns (new_string, index) with index -1 when nothing is found.
    """
    i = start
    while True:
        n = len(s)
        while i < n:
            c = s[i]
            if c in stops or (blank and c in BLANKS) or (ignorevars and c == "$"):
                break
            i += 1
        if i >= n:
            return s, -1
        if ignorevars and s[i] == "$":
            openp = s[i + 1] if i + 1 < n else ""
            i += 2
            if openp in ("(", "{"):
                closep = ")" if openp == "(" else "}"
                count = 1
                while i < n:
                    if s[i] == openp:
                        count += 1
                    elif s[i] == closep:
                        count -= 1
                        if count == 0:
                            i += 1
                            break
                    i += 1
            continue
        if i > start and s[i - 1] == "\\":
            k = i - 1
            while k >= start and s[k] == "\\":
                k -= 1
            nbs = i - 1 - k
            keep = nbs // 2
            s = s[:k + 1] + "\\" * keep + s[i:]
            i = k + 1 + keep
            if nbs % 2 == 0:
                return s, i
            i += 1
            continue
        return s, i


def find_percent(s):
    """Return (string, index of the unquoted %) with quoting removed."""
    return find_char_unquote(s, "%")


def pattern_matches(pattern, percent, word):
    """pattern has its active % at index percent (or -1 for none)."""
    if percent < 0:
        return pattern == word
    pre = pattern[:percent]
    post = pattern[percent + 1:]
    if len(word) < len(pre) + len(post):
        return False
    return word.startswith(pre) and word.endswith(post)


def subst_expand(text, sub, rep, by_word):
    if not sub and not by_word:
        return text + rep
    out = []
    t = 0
    n = len(text)
    slen = len(sub)
    while True:
        if by_word and slen == 0:
            p = end_of_token(text, next_token(text, t))
        else:
            p = text.find(sub, t)
            if p < 0:
                out.append(text[t:])
                return "".join(out)
        if p > t:
            out.append(text[t:p])
        if by_word and ((p > 0 and text[p - 1] not in BLANKS) or
                        (p + slen < n and text[p + slen] not in BLANKS)):
            out.append(sub)
        elif rep:
            out.append(rep)
        t = p + slen
        if t >= n:
            return "".join(out)


def patsubst_expand(text, pattern, replace, ppercent=None, rpercent=None):
    """patsubst with make's word and spacing rules.

    ppercent/rpercent are indexes of the active % (already unquoted) or None
    to look them up here.
    """
    if rpercent is None:
        replace, rpercent = find_percent(replace)
    if ppercent is None:
        pattern, ppercent = find_percent(pattern)
    if rpercent >= 0:
        r_pre, r_post = replace[:rpercent], replace[rpercent + 1:]
    else:
        r_pre, r_post = replace, ""
    if ppercent < 0:
        return subst_expand(text, pattern, replace, True)
    p_pre, p_post = pattern[:ppercent], pattern[ppercent + 1:]
    out = []
    doneany = False
    for w in words(text):
        ln = len(w)
        fail = (ln < len(p_pre) + len(p_post) or not w.startswith(p_pre)
                or not w.endswith(p_post))
        if fail:
            out.append(w)
        else:
            out.append(r_pre)
            if rpercent >= 0:
                out.append(w[len(p_pre):ln - len(p_post)])
                out.append(r_post)
        if fail or r_pre or (rpercent >= 0 and ln + len(r_post) > 0):
            out.append(" ")
            doneany = True
    s = "".join(out)
    if doneany:
        s = s[:-1]
    return s
