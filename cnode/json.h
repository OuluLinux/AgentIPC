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

#endif
