/* Loopback TCP listener for direct links. POSIX sockets. */
#ifndef CNODE_NET_H
#define CNODE_NET_H

#include <stdio.h>

/* A listening socket on 127.0.0.1:port, or -1. */
int cn_listen_loopback(int port);

/* Accepts one connection and wraps it in two streams. Returns 0, or -1 on error. */
int cn_accept(int lfd, FILE **in, FILE **out);

/* Closes both streams and their sockets. */
void cn_close(FILE *in, FILE *out);

/* A closed peer must not kill the process with SIGPIPE. */
void cn_ignore_sigpipe(void);

#endif
