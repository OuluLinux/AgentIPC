/* Wire protocol constants and the hello handshake. Matches agentipc/proto.py. */
#ifndef CNODE_PROTO_H
#define CNODE_PROTO_H

#include "json.h"

#define CN_PROTOCOL_VERSION 2
#define CN_MIN_PROTOCOL_VERSION 2
#define CN_MAX_LINE (1 << 20)

/* Refusal reasons, as sent in bye frames. */
#define CN_BAD_FRAME "bad-frame"
#define CN_VERSION "version"
#define CN_SELF_LINK "self-link"

#define CN_DEFAULT_TTL 16
#define CN_MAX_TTL 64
#define CN_MAX_MULTICAST 64

/* The link version for a peer's range, or 0 if the ranges do not overlap. */
int cn_negotiate(long peer_version, long peer_min);

/* 1 if name is 1..64 characters from [A-Za-z0-9._-]. */
int cn_valid_node(const char *name);

/* Checks a parsed hello frame for this node. On success returns NULL and sets
   *version and copies the peer's node id into peer (65 bytes). On refusal
   returns the bye reason. */
const char *cn_check_hello(const jval *frame, const char *self, int *version, char *peer);

/* Checks a parsed packet frame. Returns NULL if well formed, else the bye reason. */
const char *cn_check_packet(const jval *frame);

/* 1 if the packet's dst lists name, either alone or in a list. */
int cn_dst_includes(const jval *dst, const char *name);

/* Frame lines, each ending in a newline. Caller frees. */
char *cn_hello_line(const char *node, const char *link);
char *cn_bye_line(const char *reason);

#endif
