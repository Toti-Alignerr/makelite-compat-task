# makelite

makelite is a small, pure-Python implementation of GNU make. It reads
makefiles, works out what is out of date and runs the recipes through
`/bin/sh`, with no dependencies outside the Python standard library.

It was written from the GNU make manual. Parallel builds and recursive make
are not supported.

## Usage

```sh
python3 -m makelite [options] [VAR=value ...] [target ...]
```

With no `-f`, makelite reads `GNUmakefile`, `makefile` or `Makefile` from the
current directory, in that order.

Options:

| Option | Meaning |
| --- | --- |
| `-f FILE` | read FILE as a makefile (may be repeated) |
| `-n` | print the recipes instead of running them |
| `-s` | do not echo recipes |
| `-i` | ignore errors from recipes |
| `-k` | keep going after an error |
| `-S` | cancel `-k` |
| `-t` | touch targets instead of running their recipes |
| `-q` | question mode: run nothing; the exit status says whether anything is out of date |
| `-B` | consider every target out of date |
| `-e` | environment variables override makefile assignments |
| `-r`, `-R` | accepted; makelite has no built-in rules |

The exit status is 0 on success, 1 when `-q` finds something out of date and
2 on errors.

## Library

```python
import io, makelite

out, err = io.StringIO(), io.StringIO()
status = makelite.run(["-f", "Makefile", "all"], out, err)
```

`makelite.run(argv, stdout, stderr, environ=None)` takes the arguments
without the program name, writes everything make and the recipes print to the
two text streams and returns the exit status. `environ` defaults to
`os.environ`.

## What is supported

* Makefile syntax: rules, recipes (tab-prefixed or after `;`), comments,
  line continuations, `include` / `-include` / `sinclude`, conditionals
  (`ifeq`, `ifneq`, `ifdef`, `ifndef`, `else`, `endif`), `define` / `endef`,
  `override`, `export` / `unexport`.
* Variables: recursive (`=`), simple (`:=` and `::=`), conditional (`?=`),
  appending (`+=`) and shell (`!=`) assignments; target-specific and
  pattern-specific variables; command-line and environment variables;
  substitution references (`$(X:a=b)`, `$(X:%.c=%.o)`); automatic variables
  `$@ $< $^ $+ $? $| $*` and their `D`/`F` forms.
* Functions: `subst`, `patsubst`, `strip`, `findstring`, `filter`,
  `filter-out`, `sort`, `word`, `wordlist`, `words`, `firstword`, `lastword`,
  `dir`, `notdir`, `suffix`, `basename`, `addsuffix`, `addprefix`, `join`,
  `wildcard`, `foreach`, `call`, `if`, `or`, `and`, `value`, `eval`,
  `origin`, `flavor`, `shell`, `error`, `warning`, `info`.
* Rules: explicit rules, multiple targets, double-colon rules, order-only
  prerequisites, static pattern rules, pattern rules and chains of them
  (intermediate files are removed afterwards), old-style suffix rules,
  `vpath` and `VPATH`.
* Special targets: `.PHONY`, `.SUFFIXES`, `.DEFAULT`, `.PRECIOUS`,
  `.INTERMEDIATE`, `.SECONDARY`, `.DELETE_ON_ERROR`, `.IGNORE`, `.SILENT`,
  `.EXPORT_ALL_VARIABLES`; `.DEFAULT_GOAL`, `MAKECMDGOALS`, `MAKEFILE_LIST`.
* Recipe prefixes `@`, `-` and `+`.

## Layout

| Module | Content |
| --- | --- |
| `makelite/main.py` | command line, the `Make` object, `run()` |
| `makelite/reader.py` | reading makefiles: lines, directives, variable definitions, rule lines |
| `makelite/rules.py` | the file database, rules, prerequisites, pattern rules, implicit search, vpath |
| `makelite/remake.py` | deciding what to remake, running recipes, intermediate files |
| `makelite/expand.py` | variable expansion and the built-in functions |
| `makelite/variables.py` | variables, origins, variable sets |
| `makelite/text.py` | word splitting and pattern helpers |

## Tests

```sh
python3 -m unittest discover -s tests
```
