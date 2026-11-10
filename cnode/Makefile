# Plain C89. Builds with any C compiler that has a C89 mode.
CC ?= cc
CFLAGS ?= -std=c89 -pedantic -Wall -Wextra -O2

all: cnode

cnode: main.o proto.o json.o leaf.o net.o
	$(CC) $(CFLAGS) -o $@ main.o proto.o json.o leaf.o net.o

main.o: main.c json.h proto.h leaf.h net.h
net.o: net.c net.h
leaf.o: leaf.c leaf.h json.h proto.h
proto.o: proto.c proto.h json.h
json.o: json.c json.h

test_json: test_json.c json.o
	$(CC) $(CFLAGS) -o $@ test_json.c json.o

check: cnode test_json
	./test_json

clean:
	rm -f cnode test_json *.o

.PHONY: all check clean
