/* agentipc C node. Handshake only so far: see cnode/README.md. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "json.h"
#include "proto.h"

#define EXIT_OK 0
#define EXIT_REFUSED 2
#define EXIT_USAGE 1

static void usage(void)
{
    fprintf(stderr, "usage: cnode --stdio --node NAME [--link ssh|direct]\n");
}

/* Reads one line without its newline. NULL at end of input or if the line is too long. */
static char *read_line(FILE *in, size_t *len_out, int *too_long)
{
    char *buf;
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
            char *grown;
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

/* Sends our hello, reads the peer's, and checks it. Returns 0 if the link may
   carry traffic. Otherwise sends a bye and returns EXIT_REFUSED. */
static int handshake(FILE *in, FILE *out, const char *node, const char *link, int *version)
{
    char *line;
    size_t len;
    int too_long;
    jval *frame;
    const char *reason, *peer = NULL;

    send_line(out, cn_hello_line(node, link));
    line = read_line(in, &len, &too_long);
    if (line == NULL) {
        if (too_long) {
            send_line(out, cn_bye_line(CN_BAD_FRAME));
            return EXIT_REFUSED;
        }
        return EXIT_REFUSED;
    }
    frame = json_parse(line, len);
    free(line);
    if (frame == NULL) {
        send_line(out, cn_bye_line(CN_BAD_FRAME));
        return EXIT_REFUSED;
    }
    reason = cn_check_hello(frame, node, version, &peer);
    if (reason != NULL) {
        send_line(out, cn_bye_line(reason));
        json_free(frame);
        return EXIT_REFUSED;
    }
    fprintf(stderr, "linked to %s at version %d\n", peer, *version);
    json_free(frame);
    return 0;
}

int main(int argc, char **argv)
{
    const char *node = NULL, *link = "ssh";
    int stdio = 0, version = 0, i;

    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--stdio") == 0) {
            stdio = 1;
        } else if (strcmp(argv[i], "--node") == 0 && i + 1 < argc) {
            node = argv[++i];
        } else if (strcmp(argv[i], "--link") == 0 && i + 1 < argc) {
            link = argv[++i];
        } else {
            usage();
            return EXIT_USAGE;
        }
    }
    if (!stdio || node == NULL || !cn_valid_node(node)) {
        usage();
        return EXIT_USAGE;
    }
    if (strcmp(link, "ssh") != 0 && strcmp(link, "direct") != 0) {
        usage();
        return EXIT_USAGE;
    }
    return handshake(stdin, stdout, node, link, &version);
}
