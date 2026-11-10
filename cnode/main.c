/* agentipc C node: a leaf with one link. See cnode/README.md. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "json.h"
#include "leaf.h"
#include "net.h"
#include "proto.h"

#define EXIT_OK 0
#define EXIT_REFUSED 2
#define EXIT_USAGE 1

static void usage(void)
{
    fprintf(stderr, "usage: cnode --stdio --node NAME [--link ssh|direct] [--announce-interval SECONDS]\n"
                    "       cnode --listen PORT --node NAME [--announce-interval SECONDS]\n");
}

/* Reads one line without its newline. NULL at end of input, or if the line is too long
   (too_long is then set). */
static char *read_line(FILE *in, size_t *len_out, int *too_long)
{
    char *buf, *grown;
    size_t len = 0, cap = 256;
    int c;

    *too_long = 0;
    buf = malloc(cap);
    if (buf == NULL) {
        return NULL;
    }
    while ((c = getc(in)) != EOF && c != '\n') {
        if (len + 1 >= CN_MAX_LINE) {
            *too_long = 1;
            free(buf);
            return NULL;
        }
        if (len + 1 >= cap) {
            cap *= 2;
            grown = realloc(buf, cap);
            if (grown == NULL) {
                free(buf);
                return NULL;
            }
            buf = grown;
        }
        buf[len++] = (char)c;
    }
    if (c == EOF && len == 0) {
        free(buf);
        return NULL;
    }
    buf[len] = '\0';
    *len_out = len;
    return buf;
}

static void send_line(FILE *out, char *line)
{
    fputs(line, out);
    fflush(out);
    free(line);
}

static void refuse(FILE *out, const char *reason)
{
    send_line(out, cn_bye_line(reason));
}

/* Sends our hello and reads the peer's. Fills peer and version. Returns 0 if
   the link may carry traffic, otherwise sends a bye and returns EXIT_REFUSED. */
static int handshake(FILE *in, FILE *out, const char *node, const char *link,
                     char *peer, int *version)
{
    char *line;
    size_t len;
    int too_long;
    jval *frame;
    const char *reason;

    send_line(out, cn_hello_line(node, link));
    line = read_line(in, &len, &too_long);
    if (line == NULL) {
        if (too_long) {
            refuse(out, CN_BAD_FRAME);
        }
        return EXIT_REFUSED;
    }
    frame = json_parse(line, len);
    free(line);
    if (frame == NULL) {
        refuse(out, CN_BAD_FRAME);
        return EXIT_REFUSED;
    }
    reason = cn_check_hello(frame, node, version, peer);
    json_free(frame);
    if (reason != NULL) {
        refuse(out, reason);
        return EXIT_REFUSED;
    }
    return 0;
}

/* Handles frames until the peer says bye or closes the link. */
static int serve(FILE *in, FILE *out, cn_leaf *lf)
{
    char *line;
    size_t len;
    int too_long;
    jval *frame;
    const jval *type;
    const char *reason;

    for (;;) {
        line = read_line(in, &len, &too_long);
        if (line == NULL) {
            if (too_long) {
                refuse(out, CN_BAD_FRAME);
                return EXIT_REFUSED;
            }
            return EXIT_OK;             /* peer closed the link */
        }
        frame = json_parse(line, len);
        free(line);
        if (frame == NULL) {
            refuse(out, CN_BAD_FRAME);
            return EXIT_REFUSED;
        }
        type = json_get(frame, "type");
        if (type == NULL || type->type != J_STR) {
            json_free(frame);
            refuse(out, CN_BAD_FRAME);
            return EXIT_REFUSED;
        }
        if (strcmp(type->str, "bye") == 0) {
            json_free(frame);
            return EXIT_OK;
        }
        if (strcmp(type->str, "packet") != 0) {
            /* A second hello, or an unknown frame type. */
            json_free(frame);
            refuse(out, CN_BAD_FRAME);
            return EXIT_REFUSED;
        }
        reason = cn_check_packet(frame);
        if (reason != NULL) {
            json_free(frame);
            refuse(out, reason);
            return EXIT_REFUSED;
        }
        cn_leaf_packet(lf, frame, out);
        json_free(frame);
    }
}

/* One link from start to finish: handshake, then the leaf loop. */
static int run_link(FILE *in, FILE *out, const char *node, const char *link, int interval)
{
    char peer[65];
    int version = 0, status;
    cn_leaf lf;

    status = handshake(in, out, node, link, peer, &version);
    if (status != 0) {
        return status;
    }
    fprintf(stderr, "linked to %s at version %d\n", peer, version);
    cn_leaf_init(&lf, node, peer, interval);
    cn_leaf_link_up(&lf, out);
    return serve(in, out, &lf);
}

/* Accepts loopback connections for ever. Each starts with "MODE link" (a direct
   link, as Python's direct dialer sends) and is served until it ends. */
static int run_listener(int port, const char *node, int interval)
{
    int lfd;
    FILE *in, *out;
    char *line;
    size_t len;
    int too_long;

    lfd = cn_listen_loopback(port);
    if (lfd < 0) {
        fprintf(stderr, "cnode: cannot listen on 127.0.0.1:%d\n", port);
        return EXIT_USAGE;
    }
    fprintf(stderr, "listening on 127.0.0.1:%d\n", port);
    for (;;) {
        if (cn_accept(lfd, &in, &out) < 0) {
            continue;
        }
        line = read_line(in, &len, &too_long);
        if (line != NULL && strcmp(line, "MODE link") == 0) {
            free(line);
            run_link(in, out, node, "direct", interval);
        } else {
            free(line);
        }
        cn_close(in, out);
    }
}

int main(int argc, char **argv)
{
    const char *node = NULL, *link = "ssh";
    int stdio = 0, port = 0, interval = CN_ANNOUNCE_INTERVAL, i;

    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--stdio") == 0) {
            stdio = 1;
        } else if (strcmp(argv[i], "--listen") == 0 && i + 1 < argc) {
            port = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--node") == 0 && i + 1 < argc) {
            node = argv[++i];
        } else if (strcmp(argv[i], "--link") == 0 && i + 1 < argc) {
            link = argv[++i];
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
    cn_ignore_sigpipe();
    if (port > 0 && port < 65536 && !stdio) {
        return run_listener(port, node, interval);
    }
    if (!stdio || port != 0) {
        usage();
        return EXIT_USAGE;
    }
    if (strcmp(link, "ssh") != 0 && strcmp(link, "direct") != 0) {
        usage();
        return EXIT_USAGE;
    }
    return run_link(stdin, stdout, node, link, interval);
}
