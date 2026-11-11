/* Minimal JSON reader for wire frames. C89, no dependencies. */
#ifndef CNODE_JSON_H
#define CNODE_JSON_H

#include <stddef.h>

enum jtype { J_NULL, J_BOOL, J_NUM, J_STR, J_ARR, J_OBJ };

typedef struct jval jval;
struct jval {
    int type;
    int boolean;        /* J_BOOL */
    double num;         /* J_NUM */
    char *str;          /* J_STR */
    size_t n;           /* J_ARR, J_OBJ: element count */
    jval **items;       /* J_ARR elements, J_OBJ values */
    char **keys;        /* J_OBJ keys */
};

/* Parses one complete JSON value that fills text[0..len). NULL on any error. */
jval *json_parse(const char *text, size_t len);
/* The value under key in an object, or NULL. */
const jval *json_get(const jval *obj, const char *key);
void json_free(jval *v);

/* A growing byte buffer for building JSON text. */
typedef struct sbuf sbuf;
struct sbuf {
    char *data;
    size_t len;
    size_t cap;
};

/* Each returns 0 on success, -1 on out of memory. */
int sb_add(sbuf *b, const char *s, size_t n);
int sb_adds(sbuf *b, const char *s);
void sb_free(sbuf *b);

/* Appends s as a JSON string, quotes and escapes included. */
int json_write_string(sbuf *b, const char *s);

/* Appends the JSON text of v. Output is ASCII: non-ASCII text becomes \uXXXX escapes,
   as Python's json.dumps does. Returns 0 on success, -1 on out of memory. */
int json_write(sbuf *b, const jval *v);

#endif
