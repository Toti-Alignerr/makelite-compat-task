import unittest

from helpers import Workdir


class VariableTests(unittest.TestCase):
    def run_make(self, makefile, *argv):
        w = Workdir(makefile)
        try:
            return w.run(*argv)
        finally:
            w.close()

    def test_recursive_and_simple(self):
        mk = "A = $(B)\nB = late\nC := $(B)x\nB = later\nall: ; @echo [$(A)] [$(C)]\n"
        self.assertEqual(self.run_make(mk), (0, "[later] [latex]\n", ""))

    def test_append_and_conditional(self):
        mk = "X = 1\nX += 2\nY ?= y\nY ?= z\nall: ; @echo [$(X)] [$(Y)]\n"
        self.assertEqual(self.run_make(mk), (0, "[1 2] [y]\n", ""))

    def test_command_line_variable(self):
        mk = "X = file\nall: ; @echo [$(X)] $(origin X)\n"
        self.assertEqual(self.run_make(mk, "X=cmd"), (0, "[cmd] command line\n", ""))

    def test_target_specific_variable(self):
        mk = "X = global\nt: X = local\nt: ; @echo [$(X)]\n"
        self.assertEqual(self.run_make(mk), (0, "[local]\n", ""))


if __name__ == "__main__":
    unittest.main()
