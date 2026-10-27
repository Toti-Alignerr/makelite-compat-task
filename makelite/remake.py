"""Deciding what to remake and running recipes."""

import os
import subprocess
import time

from .rules import (CS_NOT_STARTED, CS_DEPS_RUNNING, CS_RUNNING, CS_FINISHED,
                    NONEXISTENT, NEW_MTIME, COMMANDS_SILENT, COMMANDS_RECURSE,
                    COMMANDS_NOERROR)
from .text import BLANKS, next_token
from .variables import (EXPORT_DEFAULT, EXPORT_YES, EXPORT_NO, O_DEFAULT,
                        O_AUTOMATIC, O_COMMAND, O_ENV, O_ENV_OVERRIDE)


def root(f):
    return f.double_colon if f.double_colon is not None else f


class Remake:
    # ----------------------------------------------------------- mtimes
    def name_mtime(self, name):
        try:
            return os.stat(name).st_mtime_ns
        except OSError:
            return NONEXISTENT

    def file_mtime(self, f, search=True):
        if f.last_mtime is not None:
            return f.last_mtime
        mtime = self.name_mtime(f.name)
        if mtime == NONEXISTENT and search and not f.ignore_vpath:
            found = self.vpath_search(f.name)
            if found is not None:
                f.vpath_name = found
                f.hname = found
                self.files.setdefault(found, f)
                mtime = self.name_mtime(found)
        f.last_mtime = mtime
        return mtime

    # ------------------------------------------------------- goal chain
    def update_goal_chain(self, goals):
        goals = [[g, False] for g in goals]
        status = -1
        while goals:
            i = 0
            while i < len(goals):
                g = goals[i]
                gf = g[0]
                stop = False
                any_not_updated = False
                f = root(gf)
                while f is not None:
                    ocs = self.commands_started
                    x = self.update_file(f, 0)
                    if self.commands_started > ocs:
                        g[1] = True
                    stop = False
                    if (x != 0 or f.updated) and status < 1:
                        if f.update_status != 0:
                            status = f.update_status
                            stop = self.question and not self.keep_going
                        else:
                            mtime = self.file_mtime(f)
                            if f.updated and g[1] and mtime != f.mtime_before_update:
                                status = 0
                    any_not_updated = any_not_updated or not f.updated
                    if stop:
                        break
                    f = f.prev
                f = gf
                if stop or not any_not_updated:
                    if (f.update_status == 0 and not g[1] and not self.silent
                            and not self.question):
                        if f.phony or f.cmds is None:
                            self.message("Nothing to be done for '%s'." % f.name)
                        else:
                            self.message("'%s' is up to date." % f.name)
                    del goals[i]
                    if stop:
                        goals = []
                        break
                else:
                    i += 1
            self.considered_flag = not self.considered_flag
        return status

    # ------------------------------------------------------- update_file
    def update_file(self, file, depth):
        f = root(file)
        if f.considered == self.considered_flag:
            return f.update_status if f.command_state == CS_FINISHED else 0
        status = 0
        while f is not None:
            f.considered = self.considered_flag
            status |= self.update_file_1(f, depth)
            if status != 0 and not self.keep_going:
                return status
            if f.command_state in (CS_RUNNING, CS_DEPS_RUNNING):
                status = 0
                break
            f = f.prev
        return status

    def start_updating(self, f):
        root(f).updating = True

    def finish_updating(self, f):
        root(f).updating = False

    def is_updating(self, f):
        return root(f).updating

    def update_file_1(self, file, depth):
        if file.updated:
            if file.update_status > 0:
                return file.update_status
            return 0
        if file.command_state == CS_RUNNING:
            return 0
        if file.command_state == CS_FINISHED:
            return file.update_status
        depth += 1
        self.start_updating(file)
        this_mtime = self.file_mtime(file)
        noexist = this_mtime == NONEXISTENT
        must_make = noexist
        if not file.phony and file.cmds is None and not file.tried_implicit:
            self.try_implicit_rule(file, depth)
            file.tried_implicit = True
        default = self.lookup_file(".DEFAULT")
        if (file.cmds is None and not file.is_target and default is not None
                and default.cmds is not None):
            file.cmds = default.cmds
        dep_status = 0
        k = 0
        while k < len(file.deps):
            d = file.deps[k]
            mtime = self.file_mtime(d.file)
            if self.is_updating(d.file):
                self.error(None, "Circular %s <- %s dependency dropped."
                           % (file.name, d.file.name))
                del file.deps[k]
                continue
            d.file.parent = file
            maybe_make = [must_make]
            dep_status |= self.check_dep(d.file, depth, this_mtime, maybe_make)
            if not d.ignore_mtime:
                must_make = maybe_make[0]
            if dep_status != 0 and not self.keep_going:
                break
            d.changed = self.file_mtime(d.file) != mtime
            k += 1
        if must_make or self.always_make:
            for d in file.deps:
                if d.file.intermediate:
                    mtime = self.file_mtime(d.file)
                    d.file.parent = file
                    dep_status |= self.update_file(d.file, depth)
                    if dep_status != 0 and not self.keep_going:
                        break
                    d.changed = ((file.phony and file.cmds is not None)
                                 or self.file_mtime(d.file) != mtime)
        self.finish_updating(file)
        if dep_status != 0:
            file.update_status = dep_status
            self.notice_finished_file(file)
            depth -= 1
            return dep_status
        if file.command_state == CS_DEPS_RUNNING:
            file.command_state = CS_NOT_STARTED
        deps_changed = False
        for d in file.deps:
            d_mtime = self.file_mtime(d.file)
            if not d.ignore_mtime:
                if d_mtime == NONEXISTENT and not d.file.intermediate:
                    must_make = True
                deps_changed = deps_changed or d.changed
            d.changed = d.changed or noexist or d_mtime > this_mtime
        depth -= 1
        if file.double_colon is not None and not file.deps:
            must_make = True
        elif (not noexist and file.is_target and not deps_changed
              and file.cmds is None and not self.always_make):
            must_make = False
        elif not must_make and file.cmds is not None and self.always_make:
            must_make = True
        if not must_make:
            self.notice_finished_file(file)
            g = file
            while g is not None:
                if g.vpath_name is not None:
                    g.name = g.vpath_name
                g = g.prev
            return 0
        if file.vpath_name is not None:
            file.ignore_vpath = True
            file.vpath_name = None
            file.hname = file.name
            if file.mtime_before_update is None:
                file.mtime_before_update = file.last_mtime
            file.last_mtime = None
            self.file_mtime(file, search=False)
        self.remake_file(file)
        if file.command_state != CS_FINISHED:
            return 0
        file.updated = True
        return file.update_status

    def check_dep(self, file, depth, this_mtime, must_make_ptr):
        depth += 1
        self.start_updating(file)
        dep_status = 0
        if (file.phony or not file.intermediate
                or self.file_mtime(file) != NONEXISTENT):
            dep_status = self.update_file(file, depth)
            mtime = self.file_mtime(file)
            if mtime == NONEXISTENT or mtime >= this_mtime:
                must_make_ptr[0] = True
        else:
            if not file.phony and file.cmds is None and not file.tried_implicit:
                self.try_implicit_rule(file, depth)
                file.tried_implicit = True
            default = self.lookup_file(".DEFAULT")
            if (file.cmds is None and not file.is_target and default is not None
                    and default.cmds is not None):
                file.cmds = default.cmds
            mtime = self.file_mtime(file)
            if mtime != NONEXISTENT and mtime > this_mtime:
                must_make_ptr[0] = True
            else:
                k = 0
                while k < len(file.deps):
                    d = file.deps[k]
                    if self.is_updating(d.file):
                        self.error(None, "Circular %s <- %s dependency dropped."
                                   % (file.name, d.file.name))
                        del file.deps[k]
                        continue
                    d.file.parent = file
                    maybe = [must_make_ptr[0]]
                    dep_status |= self.check_dep(d.file, depth, this_mtime, maybe)
                    if not d.ignore_mtime:
                        must_make_ptr[0] = maybe[0]
                    if dep_status != 0 and not self.keep_going:
                        break
                    k += 1
        self.finish_updating(file)
        return dep_status

    # ------------------------------------------------------- remake_file
    def complain(self, file):
        if file.parent is None:
            msg = "No rule to make target '%s'" % file.name
        else:
            msg = "No rule to make target '%s', needed by '%s'" % (
                file.name, file.parent.name)
        if not self.keep_going:
            self.fatal(None, msg)
        self.error(None, "*** " + msg + ".")

    def remake_file(self, file):
        if file.cmds is None:
            if file.phony:
                file.update_status = 0
            elif file.is_target:
                file.update_status = 0
            else:
                self.complain(file)
                file.update_status = 2
        else:
            file.cmds.chop()
            if not self.touch or file.cmds.any_recurse:
                self.execute_file_commands(file)
                return
            file.update_status = 0
        self.notice_finished_file(file)

    def notice_finished_file(self, file):
        ran = file.command_state == CS_RUNNING
        touched = False
        file.command_state = CS_FINISHED
        file.updated = True
        if self.touch and file.update_status == 0:
            if True:
                if file.phony:
                    file.update_status = 0
                elif file.cmds is not None:
                    file.update_status = self.touch_file(file)
                    self.commands_started += 1
                    touched = True
        if file.mtime_before_update is None:
            file.mtime_before_update = file.last_mtime
        if (ran and not file.phony) or touched:
            i = 0
            if self.question or self.just_print or self.touch:
                lf = file.cmds.lines_flags if file.cmds is not None else []
                i = len(lf)
                while i > 0 and (lf[i - 1] & COMMANDS_RECURSE):
                    i -= 1
            elif file.is_target and file.cmds is None:
                i = 1
            file.last_mtime = None if i == 0 else NEW_MTIME
        if file.double_colon is not None:
            dc = file.double_colon
            max_m = file.last_mtime
            g = dc
            while g is not None and g.updated:
                if max_m is not None and (g.last_mtime is None or g.last_mtime > max_m):
                    max_m = g.last_mtime
                g = g.prev
            if g is None:
                g = dc
                while g is not None:
                    g.last_mtime = max_m
                    g = g.prev
        if ran and file.update_status != -1:
            for a in file.also_make:
                a.command_state = CS_FINISHED
                a.updated = True
                a.update_status = file.update_status
                if not a.phony:
                    a.last_mtime = None
                    self.file_mtime(a)
        elif file.update_status == -1:
            file.update_status = 0

    def touch_file(self, file):
        if self.just_print:
            return 0
        try:
            if os.path.exists(file.name):
                os.utime(file.name, None)
            else:
                open(file.name, "ab").close()
        except OSError as e:
            self.error(None, "touch: %s: %s" % (file.name, e.strerror))
            return 1
        return 0

    # --------------------------------------------------------- execution
    def execute_file_commands(self, file):
        text = file.cmds.raw_text()
        if all(c in " \t\n\v\f\r-@" for c in text):
            file.command_state = CS_RUNNING
            file.update_status = 0
            self.notice_finished_file(file)
            return
        self.initialize_file_variables(file)
        self.set_file_variables(file)
        file.command_state = CS_RUNNING
        setlist = self.file_setlist(file)
        env = self.target_environment(file, setlist)
        self.run_job(file, setlist, env)

    def expand_recipe_line(self, file, setlist, idx):
        """Expand one recipe line just before it runs."""
        text, lineno = file.cmds.chopped[idx]
        saved = self.setlist
        saved_loc = self.reading_loc
        self.setlist = setlist
        self.reading_loc = (file.cmds.loc[0], lineno)
        try:
            return self.expand(collapse_in_refs(text))
        finally:
            self.setlist = saved
            self.reading_loc = saved_loc

    def run_job(self, file, setlist, env):
        for idx in range(len(file.cmds.chopped)):
            p = self.expand_recipe_line(file, setlist, idx)
            while p is not None:
                flags = file.command_flags | file.cmds.lines_flags[idx]
                noerror = bool(flags & COMMANDS_NOERROR)
                i = 0
                while i < len(p):
                    c = p[i]
                    if c == "@":
                        flags |= COMMANDS_SILENT
                    elif c == "+":
                        flags |= COMMANDS_RECURSE
                    elif c == "-":
                        noerror = True
                    elif c not in BLANKS:
                        break
                    i += 1
                body = p[i:]
                end = find_command_end(body)
                if end < 0:
                    cmd = body
                    p = None
                else:
                    cmd = body[:end]
                    p = body[end + 1:]
                shell_cmd = strip_continuations(cmd)
                if shell_cmd.strip(" \t\n") == "":
                    continue
                recurse = bool(flags & COMMANDS_RECURSE)
                if self.question and not recurse:
                    file.update_status = 1
                    self.notice_finished_file(file)
                    return
                if self.touch and not recurse:
                    continue
                if self.just_print or (not (flags & COMMANDS_SILENT) and not self.silent):
                    # show continued lines joined, the way the shell runs them
                    self.out_write(" ".join(part.strip() for part in cmd.split("\\\n")) + "\n")
                self.commands_started += 1
                if self.just_print:
                    continue
                rc = self.run_shell(shell_cmd, env)
                if rc != 0:
                    if not noerror and not self.ignore_errors:
                        self.error(None, "*** [%s:%d: %s] Error %d" % (
                            file.cmds.loc[0], file.cmds.chopped[idx][1], file.name, rc))
                        file.update_status = 2
                        if self.delete_on_error():
                            self.delete_target(file, None)
                            for a in file.also_make:
                                self.delete_target(a, file.name)
                        self.notice_finished_file(file)
                        if not self.keep_going:
                            raise_die(2)
                        return
                    self.error(None, "[%s:%d: %s] Error %d (ignored)" % (
                        file.cmds.loc[0], file.cmds.chopped[idx][1], file.name, rc))
        file.update_status = 0
        self.notice_finished_file(file)

    def delete_on_error(self):
        f = self.lookup_file(".DELETE_ON_ERROR")
        return f is not None and f.is_target

    def delete_target(self, file, on_behalf_of):
        if file.precious or file.phony:
            return
        try:
            st = os.stat(file.name)
        except OSError:
            return
        import stat as _stat
        if _stat.S_ISREG(st.st_mode) and st.st_mtime_ns != file.last_mtime:
            if on_behalf_of:
                self.error(None, "*** [%s] Deleting file '%s'" % (on_behalf_of, file.name))
            else:
                self.error(None, "*** Deleting file '%s'" % file.name)
            try:
                os.unlink(file.name)
            except OSError:
                pass

    def run_shell(self, cmd, env):
        self.flush_out()
        try:
            r = subprocess.run(["/bin/sh", "-c", cmd.encode("latin-1")],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=env)
        except OSError:
            return 127
        if r.stdout:
            self.out_write(r.stdout.decode("latin-1"))
        if r.stderr:
            self.err_write(r.stderr.decode("latin-1"))
        rc = r.returncode
        if rc < 0:
            rc = 128 - rc
        return rc

    def target_environment(self, file, setlist):
        table = {}
        for vs in setlist:
            for name, v in vs.items():
                if v.per_target and v.export == EXPORT_DEFAULT:
                    gv = self.globals.get(name)
                    if gv is not None:
                        v.export = gv.export
                if v.export == EXPORT_NO:
                    continue
                if v.export == EXPORT_DEFAULT:
                    if v.origin in (O_DEFAULT, O_AUTOMATIC):
                        continue
                    if not v.exportable:
                        continue
                    if not self.export_all and v.origin not in (O_ENV, O_ENV_OVERRIDE):
                        continue
                if name not in table:
                    table[name] = v
        env = {}
        for name, v in table.items():
            if v.recursive:
                saved = self.setlist
                self.setlist = setlist
                try:
                    value = self.recursively_expand(v)
                finally:
                    self.setlist = saved
            else:
                value = v.value
            env[name.encode("latin-1")] = value.encode("latin-1")
        env[b"MAKELEVEL"] = b"1"
        return env

    # ------------------------------------------------------ intermediates
    def remove_intermediates(self):
        names = []
        for f in self.file_order:
            if (f.intermediate and (f.dontcare or not f.precious)
                    and not f.secondary and not f.cmd_target):
                if f.update_status == -1:
                    continue
                if f.mtime_before_update not in (None, NONEXISTENT):
                    continue
                if not self.just_print:
                    try:
                        os.unlink(f.name)
                    except FileNotFoundError:
                        continue
                    except OSError:
                        pass
                if not f.dontcare:
                    names.append(f.name)
        if names and not self.silent:
            self.out_write("rm " + " ".join(names) + "\n")


def raise_die(status):
    from .errors import Die
    raise Die(status)


def find_command_end(s):
    """Index of the first newline that is not a backslash continuation."""
    i = s.find("\n")
    while i >= 0:
        if i > 0 and s[i - 1] == "\\":
            i = s.find("\n", i + 1)
            continue
        return i
    return -1


def strip_continuations(cmd):
    out = []
    i = 0
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if c == "\\" and i + 1 < n and cmd[i + 1] == "\n":
            i += 2
            if i < n and cmd[i] == "\t":
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def collapse_in_refs(line):
    """Collapse backslash-newline inside $(...) references before expansion."""
    if "\\\n" not in line:
        return line
    out = []
    i = 0
    n = len(line)
    while True:
        j = line.find("$", i)
        if j < 0:
            out.append(line[i:])
            break
        out.append(line[i:j + 1])
        i = j + 1
        if i < n and line[i] in "({":
            openp = line[i]
            closep = ")" if openp == "(" else "}"
            out.append(openp)
            i += 1
            refstart = len(out)
            count = 0
            while i < n:
                c = line[i]
                if c == closep:
                    count -= 1
                    if count < 0:
                        break
                    out.append(c)
                    i += 1
                elif c == "\\" and i + 1 < n and line[i + 1] == "\n":
                    q = i - 1
                    quoted = False
                    while q >= 0 and line[q] == "\\":
                        quoted = not quoted
                        q -= 1
                    if quoted:
                        out.append(c)
                        i += 1
                    else:
                        i = next_token(line, i + 2)
                        while len(out) > refstart and out[-1] in BLANKS:
                            out.pop()
                        out.append(" ")
                else:
                    if c == openp:
                        count += 1
                    out.append(c)
                    i += 1
    return "".join(out)
