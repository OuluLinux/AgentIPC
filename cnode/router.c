/* The C node's event loop, links, topology and forwarding. Matches agentipc/node.py. */
#define _POSIX_C_SOURCE 200112L

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>

#include "proto.h"
#include "router.h"

enum { LINK_MODE, LINK_HELLO, LINK_UP };

#define ANNOUNCE_TTL CN_DEFAULT_TTL

/* ----- small helpers ------------------------------------------------- */

static void new_id(cn_router *r, char *buf)
{
    r->counter++;
    sprintf(buf, "%08lx%08lx", r->epoch, r->counter);
}

/* Writes all of text to a link. A failed write closes the link. */
static int link_write(cn_router *r, int slot, const char *text, size_t len)
{
    cn_link *l = &r->links[slot];
    size_t done = 0;

    while (done < len) {
        ssize_t n = write(l->out_fd, text + done, len - done);
        if (n < 0 && errno == EINTR) {
            continue;
        }
        if (n <= 0) {
            return -1;
        }
        done += (size_t)n;
    }
    return 0;
}

static void send_text(cn_router *r, int slot, char *text)
{
    if (text != NULL) {
        if (link_write(r, slot, text, strlen(text)) < 0) {
            r->links[slot].state = -1;  /* closed on the next pass */
        }
        free(text);
    }
}

/* The UP links, in slot order. */
static int up_links(cn_router *r, int *out, int max)
{
    int i, n = 0;
    for (i = 0; i < CN_MAX_LINKS && n < max; i++) {
        if (r->links[i].used && r->links[i].state == LINK_UP) {
            out[n++] = i;
        }
    }
    return n;
}

/* ----- duplicate suppression ------------------------------------------ */

/* 1 if (src, id, dst) was not seen before. Remembers it either way. */
static int seen_add(cn_router *r, const char *src, const char *id, const char *dst)
{
    size_t len = strlen(src) + strlen(id) + strlen(dst) + 3;
    char *key = malloc(len);
    int i;

    if (key == NULL) {
        return 0;
    }
    sprintf(key, "%s\x01%s\x01%s", src, id, dst);
    for (i = 0; i < CN_SEEN; i++) {
        if (r->seen[i] != NULL && strcmp(r->seen[i], key) == 0) {
            free(key);
            return 0;
        }
    }
    free(r->seen[r->seen_next]);
    r->seen[r->seen_next] = key;
    r->seen_next = (r->seen_next + 1) % CN_SEEN;
    return 1;
}

/* ----- topology and routes ---------------------------------------------- */

static cn_origin *find_origin(cn_router *r, const char *name)
{
    int i;
    for (i = 0; i < CN_MAX_ORIGINS; i++) {
        if (r->origins[i].used && strcmp(r->origins[i].id, name) == 0) {
            return &r->origins[i];
        }
    }
    return NULL;
}

static int neighbours_have(const cn_origin *o, const char *name)
{
    int i;
    for (i = 0; i < o->nneigh; i++) {
        if (strcmp(o->neigh[i], name) == 0) {
            return 1;
        }
    }
    return 0;
}

static int route_known(cn_router *r, const char *name)
{
    int i;
    for (i = 0; i < r->nroutes; i++) {
        if (strcmp(r->routes[i].name, name) == 0) {
            return 1;
        }
    }
    return 0;
}

static void add_route(cn_router *r, const char *name, int link)
{
    if (r->nroutes < (int)(sizeof r->routes / sizeof r->routes[0]) && !route_known(r, name)) {
        strcpy(r->routes[r->nroutes].name, name);
        r->routes[r->nroutes].link = link;
        r->nroutes++;
    }
}

/* Breadth-first: a neighbour is reached through a link only if both ends announce
   each other, the same rule as routing.Topology._linked. Direct peers are reached
   over their link. */
static void recompute_routes(cn_router *r)
{
    int i, head = 0;

    r->nroutes = 0;
    for (i = 0; i < CN_MAX_LINKS; i++) {
        if (r->links[i].used && r->links[i].state == LINK_UP) {
            add_route(r, r->links[i].peer, i);
        }
    }
    while (head < r->nroutes) {
        cn_route here = r->routes[head++];
        cn_origin *o = find_origin(r, here.name);
        int k;
        if (o == NULL) {
            continue;
        }
        for (k = 0; k < o->nneigh; k++) {
            cn_origin *far;
            if (strcmp(o->neigh[k], r->self) == 0) {
                continue;
            }
            far = find_origin(r, o->neigh[k]);
            if (far != NULL && neighbours_have(far, here.name)) {
                add_route(r, o->neigh[k], here.link);
            }
        }
    }
}

/* The link slot that reaches name, or -1. */
static int route_for(cn_router *r, const char *name)
{
    int i;
    for (i = 0; i < r->nroutes; i++) {
        if (strcmp(r->routes[i].name, name) == 0) {
            return r->routes[i].link;
        }
    }
    return -1;
}

static void expire_origins(cn_router *r, time_t now)
{
    int i;
    int changed = 0;
    for (i = 0; i < CN_MAX_ORIGINS; i++) {
        cn_origin *o = &r->origins[i];
        if (o->used && now - o->heard > CN_NODE_TIMEOUT) {
            free(o->line);
            memset(o, 0, sizeof *o);
            changed = 1;
        }
    }
    if (changed) {
        recompute_routes(r);
    }
}

/* ----- packets we send ------------------------------------------------ */

/* Builds one packet line and writes it to a link. names are the destinations: one name
   (dst is a string) or a list (as_list). payload is JSON text. */
static void emit(cn_router *r, int slot, const char *src, const char **names, int n, int as_list,
                 const char *id, int ttl, const char *kind, const char *payload)
{
    sbuf b = {NULL, 0, 0};
    int i;
    char num[24];

    if (sb_adds(&b, "{\"type\": \"packet\", \"src\": ") < 0 || json_write_string(&b, src) < 0 ||
        sb_adds(&b, ", \"dst\": ") < 0) {
        sb_free(&b);
        return;
    }
    if (as_list) {
        sb_adds(&b, "[");
        for (i = 0; i < n; i++) {
            if (i > 0) sb_adds(&b, ", ");
            json_write_string(&b, names[i]);
        }
        sb_adds(&b, "]");
    } else {
        json_write_string(&b, names[0]);
    }
    sb_adds(&b, ", \"id\": ");
    json_write_string(&b, id);
    sprintf(num, "%d", ttl);
    sb_adds(&b, ", \"ttl\": ");
    sb_adds(&b, num);
    sb_adds(&b, ", \"kind\": ");
    json_write_string(&b, kind);
    sb_adds(&b, ", \"payload\": ");
    sb_adds(&b, payload);
    sb_adds(&b, "}\n");
    send_text(r, slot, b.data);
    b.data = NULL;
    sb_free(&b);
}

/* Our own announce: our link set, to every UP link. */
static void announce(cn_router *r)
{
    int slots[CN_MAX_LINKS], n, i;
    sbuf payload = {NULL, 0, 0};
    char id[32], seq[32];

    n = up_links(r, slots, CN_MAX_LINKS);
    r->seq += 1.0;
    r->last_announce = time(NULL);
    if (n == 0) {
        return;
    }
    sprintf(seq, "%.0f", r->seq);
    sb_adds(&payload, "{\"seq\": ");
    sb_adds(&payload, seq);
    sb_adds(&payload, ", \"links\": [");
    for (i = 0; i < n; i++) {
        if (i > 0) sb_adds(&payload, ", ");
        json_write_string(&payload, r->links[slots[i]].peer);
    }
    sb_adds(&payload, "], \"features\": [\"core\"]}");
    new_id(r, id);
    for (i = 0; i < n; i++) {
        const char *dst = "*";
        emit(r, slots[i], r->self, &dst, 1, 0, id, ANNOUNCE_TTL, "announce", payload.data);
    }
    sb_free(&payload);
}

/* Replays a stored announce to a new link, one ttl lower, as node.py does. */
static void replay(cn_router *r, int slot)
{
    int i;
    for (i = 0; i < CN_MAX_ORIGINS; i++) {
        cn_origin *o = &r->origins[i];
        jval *frame;
        const jval *ttl;
        sbuf b = {NULL, 0, 0};
        if (!o->used || o->line == NULL || o->ttl <= 1) {
            continue;
        }
        frame = json_parse(o->line, strlen(o->line));
        if (frame == NULL) {
            continue;
        }
        ttl = json_get(frame, "ttl");
        if (ttl != NULL) {
            ((jval *)ttl)->num = o->ttl - 1;
        }
        if (json_write(&b, frame) == 0) {
            sb_adds(&b, "\n");
            send_text(r, slot, b.data);
            b.data = NULL;
        }
        sb_free(&b);
        json_free(frame);
    }
}

/* ----- link lifecycle -------------------------------------------------- */

static int find_free_link(cn_router *r)
{
    int i;
    for (i = 0; i < CN_MAX_LINKS; i++) {
        if (!r->links[i].used) {
            return i;
        }
    }
    return -1;
}

static void close_link(cn_router *r, int slot)
{
    cn_link *l = &r->links[slot];
    int was_up = (l->state == LINK_UP);

    if (l->owns_fd) {
        close(l->in_fd);
    }
    free(l->rbuf);
    memset(l, 0, sizeof *l);
    l->in_fd = -1;
    l->out_fd = -1;
    recompute_routes(r);
    if (was_up) {
        announce(r);
    }
}

static void refuse(cn_router *r, int slot, const char *reason)
{
    char *bye = cn_bye_line(reason);
    send_text(r, slot, bye);
    r->refused = 1;
    close_link(r, slot);
}

static void start_hello(cn_router *r, int slot)
{
    char *hello = cn_hello_line(r->self, r->links[slot].owns_fd ? "direct" : "ssh");
    send_text(r, slot, hello);
    r->links[slot].state = LINK_HELLO;
}

int cn_router_add_stdio(cn_router *r)
{
    int slot = find_free_link(r);
    if (slot < 0) {
        return -1;
    }
    memset(&r->links[slot], 0, sizeof r->links[slot]);
    r->links[slot].used = 1;
    r->links[slot].in_fd = 0;
    r->links[slot].out_fd = 1;
    r->links[slot].owns_fd = 0;
    start_hello(r, slot);
    return 0;
}

void cn_router_listen(cn_router *r, int lfd)
{
    r->lfd = lfd;
}

static void accept_link(cn_router *r)
{
    int fd = accept(r->lfd, NULL, NULL);
    int slot;

    if (fd < 0) {
        return;
    }
    slot = find_free_link(r);
    if (slot < 0) {
        close(fd);
        return;
    }
    memset(&r->links[slot], 0, sizeof r->links[slot]);
    r->links[slot].used = 1;
    r->links[slot].state = LINK_MODE;
    r->links[slot].in_fd = fd;
    r->links[slot].out_fd = fd;
    r->links[slot].owns_fd = 1;
}

/* ----- frames ---------------------------------------------------------- */

static void store_announce(cn_router *r, const jval *frame, const char *src, time_t now)
{
    const jval *payload = json_get(frame, "payload");
    const jval *seq = json_get(payload, "seq");
    const jval *links = json_get(payload, "links");
    cn_origin *o;
    size_t i;
    sbuf b = {NULL, 0, 0};

    if (strcmp(src, r->self) == 0 || seq == NULL || seq->type != J_NUM || links == NULL ||
        links->type != J_ARR || links->n > CN_MAX_NEIGH) {
        return;
    }
    for (i = 0; i < links->n; i++) {
        if (links->items[i]->type != J_STR || !cn_valid_node(links->items[i]->str)) {
            return;
        }
    }
    o = find_origin(r, src);
    if (o == NULL) {
        int k;
        for (k = 0; k < CN_MAX_ORIGINS && o == NULL; k++) {
            if (!r->origins[k].used) {
                o = &r->origins[k];
            }
        }
        if (o == NULL) {
            return;
        }
        memset(o, 0, sizeof *o);
        o->used = 1;
        strcpy(o->id, src);
    } else if (seq->num <= o->seq) {
        return;
    }
    if (json_write(&b, frame) < 0) {
        sb_free(&b);
        return;
    }
    free(o->line);
    o->line = b.data;
    b.data = NULL;
    sb_free(&b);
    o->seq = seq->num;
    o->ttl = (int)json_get(frame, "ttl")->num;
    o->heard = now;
    o->nneigh = (int)links->n;
    for (i = 0; i < links->n; i++) {
        strcpy(o->neigh[i], links->items[i]->str);
    }
    recompute_routes(r);
}

/* Relays a flood to every UP link but the one it came from. */
static void relay_flood(cn_router *r, int from, const jval *frame, const char *src, const char *id,
                        const char *kind, int ttl)
{
    int slots[CN_MAX_LINKS], n, i;
    sbuf payload = {NULL, 0, 0};
    const char *dst = "*";

    n = up_links(r, slots, CN_MAX_LINKS);
    if (json_write(&payload, json_get(frame, "payload")) < 0) {
        sb_free(&payload);
        return;
    }
    for (i = 0; i < n; i++) {
        if (slots[i] != from) {
            emit(r, slots[i], src, &dst, 1, 0, id, ttl - 1, kind, payload.data);
        }
    }
    sb_free(&payload);
}

/* A ping addressed to us gets a pong to its source, routed back. */
static void answer_ping(cn_router *r, const char *src, const jval *frame)
{
    const jval *t = json_get(json_get(frame, "payload"), "t");
    sbuf payload = {NULL, 0, 0};
    char id[32];
    int slot = route_for(r, src);

    if (slot < 0) {
        return;
    }
    sb_adds(&payload, "{\"t\": ");
    if (t == NULL || json_write(&payload, t) < 0) {
        sb_adds(&payload, "null");
    }
    sb_adds(&payload, "}");
    new_id(r, id);
    emit(r, slot, r->self, &src, 1, 0, id, ANNOUNCE_TTL, "pong", payload.data);
    sb_free(&payload);
}

static void on_packet(cn_router *r, int from, const jval *frame)
{
    const char *src = json_get(frame, "src")->str;
    const char *id = json_get(frame, "id")->str;
    const char *kind = json_get(frame, "kind")->str;
    const jval *dst = json_get(frame, "dst");
    int ttl = (int)json_get(frame, "ttl")->num;

    if (dst->type == J_STR && strcmp(dst->str, "*") == 0) {
        if (!seen_add(r, src, id, "*")) {
            return;
        }
        if (strcmp(kind, "announce") == 0) {
            store_announce(r, frame, src, time(NULL));
        }
        if (ttl > 1) {
            relay_flood(r, from, frame, src, id, kind, ttl);
        }
        return;
    }

    {
        /* Unicast or multicast. Each destination is handled once; the rest go on. */
        const char *names[CN_MAX_MULTICAST];
        int slots[CN_MAX_LINKS];
        int n = 0, i, k, nslots = 0;
        int local = 0;
        int list = (dst->type == J_ARR);
        size_t count = list ? dst->n : 1;

        for (i = 0; i < (int)count; i++) {
            const char *name = list ? dst->items[i]->str : dst->str;
            if (!seen_add(r, src, id, name)) {
                continue;
            }
            if (strcmp(name, r->self) == 0) {
                local = 1;
                continue;
            }
            if (ttl > 1) {
                int link = route_for(r, name);
                if (link >= 0) {
                    names[n++] = name;
                    for (k = 0; k < nslots && slots[k] != link; k++) {
                        ;
                    }
                    if (k == nslots) {
                        slots[nslots++] = link;
                    }
                }
            }
        }
        if (local && strcmp(kind, "ping") == 0) {
            answer_ping(r, src, frame);
        }
        for (k = 0; k < nslots; k++) {
            /* One copy per link, carrying only the destinations routed through it. */
            const char *group[CN_MAX_MULTICAST];
            int g = 0;
            sbuf payload = {NULL, 0, 0};
            for (i = 0; i < n; i++) {
                if (route_for(r, names[i]) == slots[k]) {
                    group[g++] = names[i];
                }
            }
            if (json_write(&payload, json_get(frame, "payload")) == 0) {
                emit(r, slots[k], src, group, g, list, id, ttl - 1, kind, payload.data);
            }
            sb_free(&payload);
        }
    }
}

/* Handles one complete line from a link. */
static void handle_line(cn_router *r, int slot, char *line, size_t len)
{
    cn_link *l = &r->links[slot];
    jval *frame;
    const jval *type;
    const char *reason;
    int version = 0, i;
    char peer[65];

    if (l->state == LINK_MODE) {
        if (len == 9 && strcmp(line, "MODE link") == 0) {
            start_hello(r, slot);
        } else {
            close_link(r, slot);
        }
        return;
    }
    frame = json_parse(line, len);
    if (frame == NULL) {
        refuse(r, slot, CN_BAD_FRAME);
        return;
    }
    if (l->state == LINK_HELLO) {
        reason = cn_check_hello(frame, r->self, &version, peer);
        json_free(frame);
        if (reason != NULL) {
            refuse(r, slot, reason);
            return;
        }
        /* A new link from a peer replaces an older one to the same peer: the old link
           is stale (the peer reconnected), and routes must not point at it. */
        for (i = 0; i < CN_MAX_LINKS; i++) {
            if (i != slot && r->links[i].used && r->links[i].state == LINK_UP &&
                strcmp(r->links[i].peer, peer) == 0) {
                close_link(r, i);
            }
        }
        strcpy(l->peer, peer);
        l->state = LINK_UP;
        fprintf(stderr, "linked to %s at version %d\n", peer, version);
        announce(r);
        recompute_routes(r);
        replay(r, slot);
        return;
    }
    type = json_get(frame, "type");
    if (type == NULL || type->type != J_STR) {
        json_free(frame);
        refuse(r, slot, CN_BAD_FRAME);
        return;
    }
    if (strcmp(type->str, "bye") == 0) {
        json_free(frame);
        close_link(r, slot);
        return;
    }
    if (strcmp(type->str, "packet") != 0) {
        json_free(frame);
        refuse(r, slot, CN_BAD_FRAME);
        return;
    }
    reason = cn_check_packet(frame);
    if (reason != NULL) {
        json_free(frame);
        refuse(r, slot, reason);
        return;
    }
    on_packet(r, slot, frame);
    json_free(frame);
}

static void on_readable(cn_router *r, int slot)
{
    cn_link *l = &r->links[slot];
    char buf[4096];
    ssize_t n;

    n = read(l->in_fd, buf, sizeof buf);
    if (n < 0 && errno == EINTR) {
        return;
    }
    if (n <= 0) {
        close_link(r, slot);
        return;
    }
    if (l->rlen + (size_t)n + 1 > l->rcap) {
        size_t cap = (l->rcap == 0) ? 1024 : l->rcap;
        char *grown;
        while (l->rlen + (size_t)n + 1 > cap) {
            cap *= 2;
        }
        grown = realloc(l->rbuf, cap);
        if (grown == NULL) {
            close_link(r, slot);
            return;
        }
        l->rbuf = grown;
        l->rcap = cap;
    }
    memcpy(l->rbuf + l->rlen, buf, (size_t)n);
    l->rlen += (size_t)n;

    for (;;) {
        char *nl;
        size_t linelen;
        char *line;

        if (!r->links[slot].used) {
            return;
        }
        nl = memchr(r->links[slot].rbuf, '\n', r->links[slot].rlen);
        if (nl == NULL) {
            break;
        }
        linelen = (size_t)(nl - r->links[slot].rbuf);
        if (linelen > CN_MAX_LINE) {
            refuse(r, slot, CN_BAD_FRAME);
            return;
        }
        line = malloc(linelen + 1);
        if (line == NULL) {
            close_link(r, slot);
            return;
        }
        memcpy(line, r->links[slot].rbuf, linelen);
        line[linelen] = '\0';
        memmove(r->links[slot].rbuf, nl + 1, r->links[slot].rlen - linelen - 1);
        r->links[slot].rlen -= linelen + 1;
        handle_line(r, slot, line, linelen);
        free(line);
    }
    if (r->links[slot].used && r->links[slot].rlen > CN_MAX_LINE) {
        refuse(r, slot, CN_BAD_FRAME);
    }
}

/* ----- the loop ---------------------------------------------------------- */

void cn_router_init(cn_router *r, const char *self, int interval)
{
    time_t now = time(NULL);

    memset(r, 0, sizeof *r);
    strcpy(r->self, self);
    r->interval = interval;
    r->lfd = -1;
    r->epoch = (unsigned long)now;
    r->seq = (double)now * 1000.0;     /* clock based, as Topology does */
    r->last_announce = 0;
}

int cn_router_run(cn_router *r)
{
    for (;;) {
        fd_set rfds;
        struct timeval tv;
        int maxfd = -1, i, n, any = 0;
        time_t now;

        FD_ZERO(&rfds);
        if (r->lfd >= 0) {
            FD_SET(r->lfd, &rfds);
            maxfd = r->lfd;
        }
        for (i = 0; i < CN_MAX_LINKS; i++) {
            if (r->links[i].used) {
                any = 1;
                FD_SET(r->links[i].in_fd, &rfds);
                if (r->links[i].in_fd > maxfd) {
                    maxfd = r->links[i].in_fd;
                }
            }
        }
        if (!any && r->lfd < 0) {
            return r->refused ? 2 : 0;
        }
        tv.tv_sec = 1;
        tv.tv_usec = 0;
        n = select(maxfd + 1, &rfds, NULL, NULL, &tv);
        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            return 1;
        }
        /* Links whose writes failed are closed here, not in the middle of a send. */
        for (i = 0; i < CN_MAX_LINKS; i++) {
            if (r->links[i].used && r->links[i].state == -1) {
                close_link(r, i);
            }
        }
        if (r->lfd >= 0 && FD_ISSET(r->lfd, &rfds)) {
            accept_link(r);
        }
        for (i = 0; i < CN_MAX_LINKS; i++) {
            if (r->links[i].used && r->links[i].state != -1 && FD_ISSET(r->links[i].in_fd, &rfds)) {
                on_readable(r, i);
            }
        }
        now = time(NULL);
        if (now - r->last_announce >= r->interval) {
            announce(r);
        }
        expire_origins(r, now);
    }
}
