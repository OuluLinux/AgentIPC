/* Loopback TCP listener for direct links. POSIX sockets. */
#ifndef CNODE_NET_H
#define CNODE_NET_H

#include <stdio.h>

/* A listening socket on 127.0.0.1:port, or -1. */
int cn_listen_loopback(int port);

/* A closed peer must not kill the process with SIGPIPE. */
void cn_ignore_sigpipe(void);

#endif
