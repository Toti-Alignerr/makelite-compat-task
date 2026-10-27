import os
import subprocess
import sys
import unittest

from helpers import Workdir

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RuleTests(unittest.TestCase):
    def test_prerequisites_run_first(self):
        w = Workdir("all: a b ; @echo all\na: ; @echo a\nb: ; @echo b\n")
        try:
            self.assertEqual(w.run(), (0, "a\nb\nall\n", ""))
        finally:
            w.close()

    def test_pattern_rule(self):
        w = Workdir("all: x.o ; @echo done\n%.o: %.c ; @echo cc $< $@\n", {"x.c": ("", 0)})
        try:
            self.assertEqual(w.run(), (0, "cc x.c x.o\ndone\n", ""))
        finally:
            w.close()

    def test_newer_prerequisite_triggers_rebuild(self):
        mk = "t: s ; @echo rebuild\n"
        w = Workdir(mk, {"t": ("", 5), "s": ("", 9)})
        try:
            self.assertEqual(w.run(), (0, "rebuild\n", ""))
        finally:
            w.close()
        w = Workdir(mk, {"t": ("", 9), "s": ("", 5)})
        try:
            status, out, err = w.run()
            self.assertEqual((status, err), (0, ""))
            self.assertIn("up to date", out)
        finally:
            w.close()

    def test_failing_recipe(self):
        w = Workdir("all:\n\t@echo before\n\t@false\n\t@echo after\n")
        try:
            status, out, err = w.run()
        finally:
            w.close()
        self.assertEqual((status, out), (2, "before\n"))
        self.assertIn("Error 1", err)

    def test_command_line(self):
        w = Workdir("all: ; @echo hello from the command line\n")
        try:
            p = subprocess.run([sys.executable, "-m", "makelite"], cwd=w.path, capture_output=True, text=True,
                               env={"PATH": "/usr/bin:/bin", "PYTHONPATH": REPO})
        finally:
            w.close()
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "hello from the command line\n", ""))


if __name__ == "__main__":
    unittest.main()
