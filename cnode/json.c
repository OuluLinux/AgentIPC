/* Minimal JSON reader for wire frames. C89, no dependencies. */
#include <stdlib.h>
#include <string.h>

#include "json.h"

#define MAX_DEPTH 32

typedef struct parser {
    const char *p;
    const char *end;
    int depth;
} parser;

static jval *new_val(int type)
{
    jval *v = calloc(1, sizeof(jval));
    if (v != NULL) {
        v->type = type;
    }
    return v;
}

void json_free(jval *v)
{
    size_t i;
    if (v == NULL) {
        return;
    }
    free(v->str);
    for (i = 0; i < v->n; i++) {
        json_free(v->items[i]);
        if (v->keys != NULL) {
            free(v->keys[i]);
        }
    }
    free(v->items);
    free(v->keys);
    free(v);
}

static jval *parse_value(parser *ps);

static void skip_ws(parser *ps)
{
    while (ps->p < ps->end && (*ps->p == ' ' || *ps->p == '\t' ||
                               *ps->p == '\r' || *ps->p == '\n')) {
        ps->p++;
    }
}

static int hex_digit(int c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* Parses a string starting at the opening quote. Returns a malloc'd copy. */
static char *parse_string(parser *ps)
{
    char *out, *w;
    size_t cap;

    if (ps->p >= ps->end || *ps->p != '"') {
        return NULL;
    }
    ps->p++;
    cap = (size_t)(ps->end - ps->p) + 1;
    out = malloc(cap);
    if (out == NULL) {
        return NULL;
    }
    w = out;
    while (ps->p < ps->end && *ps->p != '"') {
        unsigned char c = (unsigned char)*ps->p;
        if (c < 0x20) {
            free(out);
            return NULL;
        }
        if (c != '\\') {
            *w++ = (char)c;
            ps->p++;
            continue;
        }
        ps->p++;
        if (ps->p >= ps->end) {
            free(out);
            return NULL;
        }
        switch (*ps->p) {
        case '"': *w++ = '"'; break;
        case '\\': *w++ = '\\'; break;
        case '/': *w++ = '/'; break;
        case 'b': *w++ = '\b'; break;
        case 'f': *w++ = '\f'; break;
        case 'n': *w++ = '\n'; break;
        case 'r': *w++ = '\r'; break;
        case 't': *w++ = '\t'; break;
        case 'u': {
            int code = 0, i;
            if (ps->end - ps->p < 5) {
                free(out);
                return NULL;
            }
            for (i = 1; i <= 4; i++) {
                int d = hex_digit((unsigned char)ps->p[i]);
                if (d < 0) {
                    free(out);
                    return NULL;
                }
                code = code * 16 + d;
            }
            /* Node names and kinds are ASCII. Other code points become '?'. */
            *w++ = (code < 0x80) ? (char)code : '?';
            ps->p += 4;
            break;
        }
        default:
            free(out);
            return NULL;
        }
        ps->p++;
    }
    if (ps->p >= ps->end) {
        free(out);
        return NULL;
    }
    ps->p++;                    /* closing quote */
    *w = '\0';
    return out;
}

static jval *parse_number(parser *ps)
{
    char buf[64];
    size_t len = 0;
    const char *start = ps->p;
    jval *v;
    char *stop;

    if (ps->p < ps->end && *ps->p == '-') ps->p++;
    if (ps->p >= ps->end || *ps->p < '0' || *ps->p > '9') return NULL;
    while (ps->p < ps->end && *ps->p >= '0' && *ps->p <= '9') ps->p++;
    if (ps->p < ps->end && *ps->p == '.') {
        ps->p++;
        if (ps->p >= ps->end || *ps->p < '0' || *ps->p > '9') return NULL;
        while (ps->p < ps->end && *ps->p >= '0' && *ps->p <= '9') ps->p++;
    }
    if (ps->p < ps->end && (*ps->p == 'e' || *ps->p == 'E')) {
        ps->p++;
        if (ps->p < ps->end && (*ps->p == '+' || *ps->p == '-')) ps->p++;
        if (ps->p >= ps->end || *ps->p < '0' || *ps->p > '9') return NULL;
        while (ps->p < ps->end && *ps->p >= '0' && *ps->p <= '9') ps->p++;
    }
    len = (size_t)(ps->p - start);
    if (len >= sizeof(buf)) return NULL;
    memcpy(buf, start, len);
    buf[len] = '\0';
    v = new_val(J_NUM);
    if (v == NULL) return NULL;
    v->num = strtod(buf, &stop);
    return v;
}

static int literal(parser *ps, const char *word)
{
    size_t n = strlen(word);
    if ((size_t)(ps->end - ps->p) < n || memcmp(ps->p, word, n) != 0) return 0;
    ps->p += n;
    return 1;
}

static jval *parse_array(parser *ps)
{
    jval *v = new_val(J_ARR);
    if (v == NULL) return NULL;
    ps->p++;                    /* [ */
    skip_ws(ps);
    if (ps->p < ps->end && *ps->p == ']') {
        ps->p++;
        return v;
    }
    for (;;) {
        jval *item, **grown;
        skip_ws(ps);
        item = parse_value(ps);
        if (item == NULL) {
            json_free(v);
            return NULL;
        }
        grown = realloc(v->items, (v->n + 1) * sizeof(jval *));
        if (grown == NULL) {
            json_free(item);
            json_free(v);
            return NULL;
        }
        v->items = grown;
        v->items[v->n++] = item;
        skip_ws(ps);
        if (ps->p < ps->end && *ps->p == ',') {
            ps->p++;
            continue;
        }
        if (ps->p < ps->end && *ps->p == ']') {
            ps->p++;
            return v;
        }
        json_free(v);
        return NULL;
    }
}

static jval *parse_object(parser *ps)
{
    jval *v = new_val(J_OBJ);
    if (v == NULL) return NULL;
    ps->p++;                    /* { */
    skip_ws(ps);
    if (ps->p < ps->end && *ps->p == '}') {
        ps->p++;
        return v;
    }
    for (;;) {
        char *key;
        jval *item, **grown;
        char **keys;
        skip_ws(ps);
        key = parse_string(ps);
        if (key == NULL) {
            json_free(v);
            return NULL;
        }
        skip_ws(ps);
        if (ps->p >= ps->end || *ps->p != ':') {
            free(key);
            json_free(v);
            return NULL;
        }
        ps->p++;
        skip_ws(ps);
        item = parse_value(ps);
        if (item == NULL) {
            free(key);
            json_free(v);
            return NULL;
        }
        grown = realloc(v->items, (v->n + 1) * sizeof(jval *));
        keys = realloc(v->keys, (v->n + 1) * sizeof(char *));
        if (grown == NULL || keys == NULL) {
            /* On failure the old arrays are still valid; the new ones were not used. */
            if (grown != NULL) v->items = grown;
            if (keys != NULL) v->keys = keys;
            free(key);
            json_free(item);
            json_free(v);
            return NULL;
        }
        v->items = grown;
        v->keys = keys;
        v->items[v->n] = item;
        v->keys[v->n] = key;
        v->n++;
        skip_ws(ps);
        if (ps->p < ps->end && *ps->p == ',') {
            ps->p++;
            continue;
        }
        if (ps->p < ps->end && *ps->p == '}') {
            ps->p++;
            return v;
        }
        json_free(v);
        return NULL;
    }
}

static jval *parse_value(parser *ps)
{
    jval *v = NULL;

    skip_ws(ps);
    if (ps->p >= ps->end) return NULL;
    if (ps->depth >= MAX_DEPTH) return NULL;
    switch (*ps->p) {
    case '{':
    case '[':
        ps->depth++;
        v = (*ps->p == '{') ? parse_object(ps) : parse_array(ps);
        ps->depth--;
        return v;
    case '"':
        v = new_val(J_STR);
        if (v == NULL) return NULL;
        v->str = parse_string(ps);
        if (v->str == NULL) {
            free(v);
            return NULL;
        }
        return v;
    case 't':
        if (!literal(ps, "true")) return NULL;
        v = new_val(J_BOOL);
        if (v != NULL) v->boolean = 1;
        return v;
    case 'f':
        if (!literal(ps, "false")) return NULL;
        return new_val(J_BOOL);
    case 'n':
        if (!literal(ps, "null")) return NULL;
        return new_val(J_NULL);
    default:
        return parse_number(ps);
    }
}

jval *json_parse(const char *text, size_t len)
{
    parser ps;
    jval *v;

    ps.p = text;
    ps.end = text + len;
    ps.depth = 0;
    v = parse_value(&ps);
    if (v == NULL) return NULL;
    skip_ws(&ps);
    if (ps.p != ps.end) {
        json_free(v);
        return NULL;
    }
    return v;
}

const jval *json_get(const jval *obj, const char *key)
{
    size_t i;
    if (obj == NULL || obj->type != J_OBJ) return NULL;
    for (i = 0; i < obj->n; i++) {
        if (strcmp(obj->keys[i], key) == 0) return obj->items[i];
    }
    return NULL;
}
