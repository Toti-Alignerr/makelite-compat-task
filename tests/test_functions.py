import unittest

from helpers import Workdir


class FunctionTests(unittest.TestCase):
    def info(self, expr):
        w = Workdir("$(info %s)\nall: ; @:\n" % expr)
        try:
            status, out, err = w.run()
        finally:
            w.close()
        self.assertEqual((status, err), (0, ""))
        return out

    def test_text_functions(self):
        self.assertEqual(self.info("$(subst a,b,banana) $(patsubst %.c,%.o,x.c y.h)"), "bbnbnb x.o y.h\n")
        self.assertEqual(self.info("$(sort c a b a) $(words a b c) $(word 2,a b c)"), "a b c 3 b\n")
        self.assertEqual(self.info("$(filter %.c,a.c b.o) $(filter-out %.c,a.c b.o)"), "a.c b.o\n")

    def test_foreach_and_call(self):
        self.assertEqual(self.info("$(foreach x,1 2,<$(x)>)"), "<1> <2>\n")
        w = Workdir("pair = $(1)-$(2)\n$(info $(call pair,a,b))\nall: ; @:\n")
        try:
            self.assertEqual(w.run(), (0, "a-b\n", ""))
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
