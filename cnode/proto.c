/* Wire protocol constants and the hello handshake. Matches agentipc/proto.py. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "proto.h"

int cn_negotiate(long peer_version, long peer_min)
{
    long top = (peer_version < CN_PROTOCOL_VERSION) ? peer_version : CN_PROTOCOL_VERSION;
    long floor = (peer_min > CN_MIN_PROTOCOL_VERSION) ? peer_min : CN_MIN_PROTOCOL_VERSION;
    return (top >= floor) ? (int)top : 0;
}

int cn_valid_node(const char *name)
{
    size_t i, len = strlen(name);
    if (len < 1 || len > 64) {
        return 0;
    }
    for (i = 0; i < len; i++) {
        char c = name[i];
        int ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                 (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-';
        if (!ok) {
            return 0;
        }
    }
    return 1;
}

/* Sets *out and returns 1 for a whole JSON number from 0 to 1000000. */
static int get_int(const jval *v, long *out)
{
    if (v == NULL || v->type != J_NUM || v->num < 0 || v->num > 1000000) {
        return 0;
    }
    if (v->num != (double)(long)v->num) {
        return 0;
    }
    *out = (long)v->num;
    return 1;
}

const char *cn_check_hello(const jval *frame, const char *self, int *version, const char **peer)
{
    const jval *type, *node, *ver, *min, *link, *features;
    long peer_version, peer_min = 0;
    size_t i;

    if (frame == NULL || frame->type != J_OBJ) {
        return CN_BAD_FRAME;
    }
    type = json_get(frame, "type");
    if (type == NULL || type->type != J_STR || strcmp(type->str, "hello") != 0) {
        return CN_BAD_FRAME;
    }
    node = json_get(frame, "node");
    ver = json_get(frame, "version");
    link = json_get(frame, "link");
    if (node == NULL || ver == NULL || link == NULL) {
        return CN_BAD_FRAME;
    }
    if (node->type != J_STR || !cn_valid_node(node->str)) {
        return CN_BAD_FRAME;
    }
    if (!get_int(ver, &peer_version)) {
        return CN_BAD_FRAME;
    }
    min = json_get(frame, "min_version");
    if (min != NULL) {
        if (!get_int(min, &peer_min)) {
            return CN_BAD_FRAME;
        }
    } else {
        peer_min = peer_version;
    }
    features = json_get(frame, "features");
    if (features != NULL) {
        if (features->type != J_ARR) {
            return CN_BAD_FRAME;
        }
        for (i = 0; i < features->n; i++) {
            if (features->items[i]->type != J_STR) {
                return CN_BAD_FRAME;
            }
        }
    }
    *version = cn_negotiate(peer_version, peer_min);
    if (*version == 0) {
        return CN_VERSION;
    }
    if (strcmp(node->str, self) == 0) {
        return CN_SELF_LINK;
    }
    *peer = node->str;
    return NULL;
}

char *cn_hello_line(const char *node, const char *link)
{
    char *line = malloc(512);
    if (line == NULL) {
        return NULL;
    }
    sprintf(line, "{\"type\": \"hello\", \"node\": \"%s\", \"version\": %d, "
                  "\"min_version\": %d, \"link\": \"%s\", \"features\": [\"core\"]}\n",
            node, CN_PROTOCOL_VERSION, CN_MIN_PROTOCOL_VERSION, link);
    return line;
}

char *cn_bye_line(const char *reason)
{
    char *line = malloc(128);
    if (line == NULL) {
        return NULL;
    }
    sprintf(line, "{\"type\": \"bye\", \"reason\": \"%s\"}\n", reason);
    return line;
}
