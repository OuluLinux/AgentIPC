/* A leaf node has one link and does no routing. Matches agentipc/node.py. */
#include <stdio.h>
#include <string.h>

#include "leaf.h"
#include "proto.h"

static void write_line(FILE *out, const char *line)
{
    fputs(line, out);
    fflush(out);
}

void cn_leaf_init(cn_leaf *lf, const char *self, const char *peer, int announce_interval)
{
    lf->self = self;
    strcpy(lf->peer, peer);
    lf->announce_interval = announce_interval;
    lf->epoch = (unsigned long)time(NULL);
    lf->seq = (double)lf->epoch * 1000.0;   /* clock based, as Topology does */
    lf->last_announce = 0;
    lf->counter = 0;
}

/* A packet id unique to this node and process start. Caller passes a 32-byte buffer. */
static void new_id(cn_leaf *lf, char *buf)
{
    lf->counter++;
    sprintf(buf, "%08lx%08lx", lf->epoch, lf->counter);
}

static void announce(cn_leaf *lf, FILE *out)
{
    char id[32];
    char line[512];

    new_id(lf, id);
    lf->seq += 1.0;
    lf->last_announce = time(NULL);
    sprintf(line, "{\"type\": \"packet\", \"src\": \"%s\", \"dst\": \"*\", \"id\": \"%s\", "
                  "\"ttl\": %d, \"kind\": \"announce\", "
                  "\"payload\": {\"seq\": %.0f, \"links\": [\"%s\"]}}\n",
            lf->self, id, CN_DEFAULT_TTL, lf->seq, lf->peer);
    write_line(out, line);
}

/* Announces again if the interval has passed. Pings arrive every few seconds,
   so this keeps us from being forgotten without a timer of our own. */
static void announce_if_due(cn_leaf *lf, FILE *out)
{
    long since = (long)(time(NULL) - lf->last_announce);
    if (since >= lf->announce_interval) {
        announce(lf, out);
    }
}

/* Writes the t value back as a JSON number. Anything that is not a finite number becomes null. */
static void pong(cn_leaf *lf, const char *dst, const jval *t, FILE *out)
{
    char id[32];
    char tbuf[40];
    char line[512];
    double x;

    if (t != NULL && t->type == J_NUM && t->num - t->num == 0.0) {
        x = t->num;
        sprintf(tbuf, "%.17g", x);
    } else {
        strcpy(tbuf, "null");
    }
    new_id(lf, id);
    sprintf(line, "{\"type\": \"packet\", \"src\": \"%s\", \"dst\": \"%s\", \"id\": \"%s\", "
                  "\"ttl\": %d, \"kind\": \"pong\", \"payload\": {\"t\": %s}}\n",
            lf->self, dst, id, CN_DEFAULT_TTL, tbuf);
    write_line(out, line);
}

void cn_leaf_link_up(cn_leaf *lf, FILE *out)
{
    announce(lf, out);
}

void cn_leaf_packet(cn_leaf *lf, const jval *frame, FILE *out)
{
    const jval *kind, *src;

    /* A leaf does not relay: packets not addressed to it are dropped. */
    if (!cn_dst_includes(json_get(frame, "dst"), lf->self)) {
        return;
    }
    kind = json_get(frame, "kind");
    src = json_get(frame, "src");
    if (strcmp(kind->str, "ping") == 0) {
        announce_if_due(lf, out);
        pong(lf, src->str, json_get(json_get(frame, "payload"), "t"), out);
    }
}
