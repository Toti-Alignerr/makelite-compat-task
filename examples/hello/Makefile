# A small example. Run it from this directory:
#   cd examples/hello && PYTHONPATH=../.. python3 -m makelite
SRCS := main.c util.c
OBJS := $(SRCS:.c=.o)

all: hello

hello: $(OBJS)
	@echo link $@ from $^

%.o: %.c
	@echo compile $< to $@

main.c util.c:
	@echo generate $@

clean:
	@echo nothing to clean

.PHONY: all clean
