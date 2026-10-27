import io
import os
import tempfile
import time

import makelite

BASE = 1600000000


class Workdir:
    """A scratch directory holding a Makefile and some files."""

    def __init__(self, makefile, files=None):
        self.dir = tempfile.TemporaryDirectory()
        self.path = self.dir.name
        self.write("Makefile", makefile, 0)
        for name, (text, offset) in (files or {}).items():
            self.write(name, text, offset)

    def write(self, name, text, offset):
        p = os.path.join(self.path, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
        os.utime(p, (BASE + offset, BASE + offset))

    def run(self, *argv, env=None):
        old = os.getcwd()
        os.chdir(self.path)
        try:
            out, err = io.StringIO(), io.StringIO()
            status = makelite.run(list(argv), out, err, env or {"PATH": "/usr/bin:/bin"})
            return status, out.getvalue(), err.getvalue()
        finally:
            os.chdir(old)

    def exists(self, name):
        return os.path.exists(os.path.join(self.path, name))

    def close(self):
        self.dir.cleanup()
