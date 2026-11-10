/* A leaf node has one link and does no routing. It answers pings addressed to
   it and announces itself. Matches agentipc/node.py. */
#ifndef CNODE_LEAF_H
#define CNODE_LEAF_H

#include <stdio.h>
#include <time.h>

#include "json.h"

#define CN_ANNOUNCE_INTERVAL 30     /* seconds, as in node.py */

typedef struct cn_leaf cn_leaf;
struct cn_leaf {
    const char *self;               /* our node id */
    char peer[65];                  /* the only neighbour */
    int announce_interval;          /* seconds between announces */
    double seq;                     /* announce sequence, starts from the clock */
    time_t last_announce;
    unsigned long counter;          /* packet ids */
    unsigned long epoch;            /* packet ids: start time, so ids differ across restarts */
};

void cn_leaf_init(cn_leaf *lf, const char *self, const char *peer, int announce_interval);

/* Called once the handshake is done. Announces so the mesh learns the link. */
void cn_leaf_link_up(cn_leaf *lf, FILE *out);

/* Handles a well-formed packet frame. Writes any replies to out. */
void cn_leaf_packet(cn_leaf *lf, const jval *frame, FILE *out);

#endif
