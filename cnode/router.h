/* The C node's event loop, links, topology and forwarding. Matches agentipc/node.py.
   Any number of links: a stdio link (ssh) and direct links from the listener. */
#ifndef CNODE_ROUTER_H
#define CNODE_ROUTER_H

#include <time.h>

#include "json.h"

#define CN_MAX_LINKS 32
#define CN_MAX_ORIGINS 64
#define CN_MAX_NEIGH 16
#define CN_SEEN 512
#define CN_NODE_TIMEOUT 120         /* seconds without an announce before an origin is forgotten */
#define CN_DEFAULT_ANNOUNCE 30      /* seconds between our own announces */

typedef struct cn_link cn_link;
typedef struct cn_origin cn_origin;
typedef struct cn_route cn_route;
typedef struct cn_router cn_router;

struct cn_link {
    int used;
    int state;                      /* LINK_MODE, LINK_HELLO or LINK_UP */
    int in_fd;
    int out_fd;
    int owns_fd;                    /* sockets are closed by us; stdio is not */
    char peer[65];                  /* set after the peer's hello */
    char *rbuf;                     /* bytes read but not yet split into lines */
    size_t rlen;
    size_t rcap;
};

struct cn_origin {
    int used;
    char id[65];
    double seq;
    char neigh[CN_MAX_NEIGH][65];
    int nneigh;
    char *line;                     /* the announce as received, replayed to new links */
    int ttl;                        /* its ttl as received */
    time_t heard;
};

struct cn_route {
    char name[65];
    int link;                       /* index into links[] that reaches name */
};

struct cn_router {
    char self[65];
    int interval;                   /* seconds between our own announces */
    double seq;                     /* our announce sequence, starts from the clock */
    time_t last_announce;
    unsigned long epoch;            /* packet ids: start time and counter, unique per process */
    unsigned long counter;
    cn_link links[CN_MAX_LINKS];
    int lfd;                        /* listening socket, or -1 */
    cn_origin origins[CN_MAX_ORIGINS];
    cn_route routes[CN_MAX_ORIGINS + CN_MAX_LINKS];
    int nroutes;
    char *seen[CN_SEEN];            /* duplicate suppression: src, id and destination */
    int seen_next;
    int refused;                    /* a link was refused with a bye */
};

void cn_router_init(cn_router *r, const char *self, int interval);

/* Makes stdin and stdout the first link. It starts with hello, as ssh links do. */
int cn_router_add_stdio(cn_router *r);

/* Accepts direct links on lfd. Each starts with "MODE link". */
void cn_router_listen(cn_router *r, int lfd);

/* Runs until there are no links and no listener. Returns 0, or 2 if a link was refused. */
int cn_router_run(cn_router *r);

#endif
