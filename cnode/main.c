/* agentipc C node. Any number of links, routing and forwarding. See cnode/README.md. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>

#include "net.h"
#include "proto.h"
#include "router.h"

#define EXIT_USAGE 1

static void usage(void)
{
    fprintf(stderr, "usage: cnode --stdio --node NAME [--announce-interval SECONDS]\n"
                    "       cnode --listen PORT --node NAME [--announce-interval SECONDS]\n");
}

int main(int argc, char **argv)
{
    const char *node = NULL;
    int stdio = 0, port = 0, interval = CN_DEFAULT_ANNOUNCE, i, lfd;
    cn_router r;

    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--stdio") == 0) {
            stdio = 1;
        } else if (strcmp(argv[i], "--listen") == 0 && i + 1 < argc) {
            port = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--node") == 0 && i + 1 < argc) {
            node = argv[++i];
        } else if (strcmp(argv[i], "--announce-interval") == 0 && i + 1 < argc) {
            interval = atoi(argv[++i]);
            if (interval < 0) {
                usage();
                return EXIT_USAGE;
            }
        } else {
            usage();
            return EXIT_USAGE;
        }
    }
    if (node == NULL || !cn_valid_node(node)) {
        usage();
        return EXIT_USAGE;
    }
    if (stdio == (port > 0)) {          /* exactly one of --stdio and --listen */
        usage();
        return EXIT_USAGE;
    }
    cn_ignore_sigpipe();
    cn_router_init(&r, node, interval);
    if (stdio) {
        if (cn_router_add_stdio(&r) < 0) {
            return EXIT_USAGE;
        }
    } else {
        lfd = cn_listen_loopback(port);
        if (lfd < 0 || port >= 65536) {
            fprintf(stderr, "cnode: cannot listen on 127.0.0.1:%d\n", port);
            return EXIT_USAGE;
        }
        cn_router_listen(&r, lfd);
        fprintf(stderr, "listening on 127.0.0.1:%d\n", port);
    }
    return cn_router_run(&r);
}
