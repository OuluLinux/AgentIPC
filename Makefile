all:
	$(MAKE) -C cnode all

check:
	$(MAKE) -C cnode check

test: check

clean:
	$(MAKE) -C cnode clean

.PHONY: all check test clean
