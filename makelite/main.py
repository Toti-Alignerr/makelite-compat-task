"""Command line handling and the top-level driver."""

import io
import os
import sys

from .errors import Die, format_loc
from .expand import Expander
from .reader import Reader, Conditionals
from .rules import Rules
from .remake import Remake
from .text import strip_space, words
from .variables import (VarSet, O_DEFAULT, O_ENV, O_FILE, O_COMMAND,
                        O_AUTOMATIC)

DEFAULT_VARIABLES = [
    ("AR", "ar"), ("ARFLAGS", "rv"), ("AS", "as"), ("CC", "cc"),
    ("CXX", "c++"), ("CPP", "$(CC) -E"), ("FC", "f77"), ("F77", "$(FC)"),
    ("LD", "ld"), ("LEX", "lex"), ("YACC", "yacc"), ("RM", "rm -f"),
    ("MAKEINFO", "makeinfo"), ("TEX", "tex"), ("CO", "co"), ("GET", "get"),
    ("LINT", "lint"), ("PC", "pc"), ("M2C", "m2c"),
    ("OUTPUT_OPTION", "-o $@"),
    ("COMPILE.c", "$(CC) $(CFLAGS) $(CPPFLAGS) $(TARGET_ARCH) -c"),
    ("LINK.o", "$(CC) $(LDFLAGS) $(TARGET_ARCH)"),
    (".LIBPATTERNS", "lib%.so lib%.a"),
]

AUTOMATIC_DF = ["@", "%", "*", "<", "?", "^", "+"]


class Make(Expander, Reader, Rules, Remake):
    def __init__(self, stdout, stderr, environ):
        self.stdout = stdout
        self.stderr = stderr
        self.shell_env = dict(environ)
        self.globals = VarSet()
        self.setlist = [self.globals]
        self.reading_loc = None
        self.exp_loc_stack = []
        self.call_max_args = 0
        self.conditionals = Conditionals()
        self.files = {}
        self.file_order = []
        self.pattern_rules = []
        self.pattern_vars = []
        self.impossible = set()
        self.read_makefiles = []
        self.vpaths_pending = []
        self.vpaths = []
        self.general_vpath = []
        self.missing_include = None
        self.all_secondary = False
        self.second_expansion = False
        self.export_all = False
        self.ignore_errors = False
        self.silent = False
        self.keep_going = False
        self.just_print = False
        self.touch = False
        self.question = False
        self.always_make = False
        self.env_overrides = False
        self.commands_started = 0
        self.considered_flag = True
        self.pfs_rest = ""

    # ------------------------------------------------------------ output
    def out_write(self, s):
        self.stdout.write(s)
        self.stdout.flush()

    def flush_out(self):
        self.stdout.flush()

    def err_write(self, s):
        self.stderr.write(s)
        self.stderr.flush()

    def message(self, text):
        self.out_write("make: " + text + "\n")

    def error(self, loc, text):
        self.stdout.flush()
        self.err_write(format_loc(loc) + text + "\n")

    def fatal(self, loc, text):
        self.stdout.flush()
        self.err_write(format_loc(loc) + "*** " + text + ".  Stop.\n")
        raise Die(2)

    # --------------------------------------------------------------- run
    def parse_args(self, argv):
        makefiles = []
        others = []
        i = 0
        n = len(argv)
        only_others = False
        while i < n:
            a = argv[i]
            i += 1
            if only_others or not a.startswith("-") or a == "-":
                others.append(a)
                continue
            if a == "--":
                only_others = True
                continue
            k = 1
            while k < len(a):
                c = a[k]
                k += 1
                if c == "f":
                    if k < len(a):
                        makefiles.append(a[k:])
                    elif i < n:
                        makefiles.append(argv[i])
                        i += 1
                    else:
                        self.err_write("make: option requires an argument -- f\n")
                        raise Die(2)
                    break
                flag = {"n": "just_print", "s": "silent", "i": "ignore_errors",
                        "k": "keep_going", "t": "touch", "q": "question",
                        "B": "always_make", "e": "env_overrides",
                        "r": None, "R": None, "S": "nokeep"}.get(c, "bad")
                if flag == "bad":
                    self.err_write("make: invalid option -- %s\n" % c)
                    raise Die(2)
                if flag == "nokeep":
                    self.keep_going = False
                elif flag is not None:
                    setattr(self, flag, True)
        return makefiles, others

    def define_initial_variables(self, environ):
        for name, value in environ.items():
            if name in ("MAKEFLAGS", "MAKELEVEL", "MFLAGS", "SHELL"):
                continue
            self.globals.define(name, value, O_ENV, True, None)
        for name, value in DEFAULT_VARIABLES:
            self.globals.define(name, value, O_DEFAULT, True, None)
        self.globals.define("SHELL", "/bin/sh", O_DEFAULT, False, None)
        self.globals.define("MAKE_VERSION", "4.3", O_DEFAULT, False, None)
        self.globals.define("MAKEFILES", "", O_DEFAULT, False, None)
        self.globals.define("SUFFIXES", "", O_DEFAULT, False, None)
        self.globals.define("MAKEFILE_LIST", "", O_FILE, False, None)
        self.globals.define(".DEFAULT_GOAL", "", O_FILE, False, None)
        self.globals.define("MAKEFLAGS", "r", O_FILE, True, None)
        self.globals.define("CURDIR", os.getcwd(), O_FILE, False, None)
        for c in AUTOMATIC_DF:
            self.globals.define(c + "D", "$(patsubst %%/,%%,$(dir $%s))" % c,
                                O_AUTOMATIC, True, None)
            self.globals.define(c + "F", "$(notdir $%s)" % c, O_AUTOMATIC,
                                True, None)

    def main(self, argv, environ):
        makefiles, others = self.parse_args(argv)
        self.define_initial_variables(environ)
        goals = []
        for a in others:
            if a == "-":
                continue
            if self.try_variable_definition(None, a, O_COMMAND, False) is not None:
                continue
            name = a
            while name.startswith("./") and len(name) > 2:
                name = name[2:]
                while name.startswith("/"):
                    name = name[1:]
            f = self.enter_file(name)
            f.cmd_target = True
            goals.append(f)
        self.enter_file(".SUFFIXES")
        self.enter_file(".DEFAULT")
        if goals:
            self.globals.define("MAKECMDGOALS", " ".join(g.name for g in goals),
                                O_DEFAULT, False, None)
        read_any = False
        if makefiles:
            for mf in makefiles:
                if not self.eval_makefile(mf):
                    self.reading_loc = None
                    self.err_write("make: %s: No such file or directory\n" % mf)
                    self.fatal(None, "No rule to make target '%s'" % mf)
                read_any = True
        else:
            for mf in ("GNUmakefile", "makefile", "Makefile"):
                if os.path.exists(mf) and mf in os.listdir("."):
                    self.eval_makefile(mf)
                    read_any = True
                    break
        self.reading_loc = None
        if self.missing_include is not None:
            nm, loc = self.missing_include
            self.fatal(None, "No rule to make target '%s'" % nm)
        self.vpaths = list(self.vpaths_pending)
        vp = self.expand("$(strip $(VPATH))")
        if vp:
            entry = self.construct_vpath_list("%", vp)
            self.general_vpath = entry[2] if entry is not None else []
        self.snap_deps()
        self.convert_to_pattern()
        if not goals:
            dg = self.globals.get(".DEFAULT_GOAL")
            name = dg.value if dg is not None else ""
            if name != "":
                f = self.lookup_file(name)
                if f is None:
                    names = self.multi_glob(self.parse_file_seq(name, "", True))
                    if len(names) > 1:
                        self.fatal(None, ".DEFAULT_GOAL contains more than one target")
                    f = self.enter_file(names[0])
                goals = [f]
        if not goals:
            if not read_any:
                self.fatal(None, "No targets specified and no makefile found")
            self.fatal(None, "No targets")
        r = self.update_goal_chain(goals)
        if r in (-1, 0):
            return 0
        if r == 1:
            return 1
        return 2


def run(argv, stdout=None, stderr=None, environ=None):
    """Run makelite with argv (without the program name).

    Returns the exit status.  Output from make and from recipes is written to
    the given text streams.
    """
    if stdout is None:
        stdout = sys.stdout
    if stderr is None:
        stderr = sys.stderr
    if environ is None:
        environ = dict(os.environ)
    m = Make(stdout, stderr, environ)
    try:
        status = m.main(list(argv), environ)
        m.remove_intermediates()
    except Die as d:
        status = d.status
    stdout.flush()
    stderr.flush()
    return status


def main():
    out = io.TextIOWrapper(sys.stdout.buffer, encoding="latin-1", newline="")
    err = io.TextIOWrapper(sys.stderr.buffer, encoding="latin-1", newline="")
    argv = [a.encode("utf-8", "surrogateescape").decode("latin-1")
            for a in sys.argv[1:]]
    env = {}
    for k, v in os.environb.items():
        env[k.decode("latin-1")] = v.decode("latin-1")
    status = run(argv, out, err, env)
    out.flush()
    err.flush()
    return status
