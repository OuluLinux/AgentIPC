/* Minimal JSON reader for wire frames. C89, no dependencies. */
#include <stdio.h>
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

/* Writes code point cp as UTF-8 at w. Returns the position after it. */
static char *put_utf8(char *w, long cp)
{
    if (cp < 0x80) {
        *w++ = (char)cp;
    } else if (cp < 0x800) {
        *w++ = (char)(0xC0 | (cp >> 6));
        *w++ = (char)(0x80 | (cp & 0x3F));
    } else if (cp < 0x10000) {
        *w++ = (char)(0xE0 | (cp >> 12));
        *w++ = (char)(0x80 | ((cp >> 6) & 0x3F));
        *w++ = (char)(0x80 | (cp & 0x3F));
    } else {
        *w++ = (char)(0xF0 | (cp >> 18));
        *w++ = (char)(0x80 | ((cp >> 12) & 0x3F));
        *w++ = (char)(0x80 | ((cp >> 6) & 0x3F));
        *w++ = (char)(0x80 | (cp & 0x3F));
    }
    return w;
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
            ps->p += 4;
            if (code >= 0xD800 && code <= 0xDBFF && ps->end - ps->p >= 7 &&
                ps->p[1] == '\\' && ps->p[2] == 'u') {
                /* A surrogate pair: combine with the low half that follows. */
                int low = 0, k;
                for (k = 3; k <= 6; k++) {
                    int d = hex_digit((unsigned char)ps->p[k]);
                    if (d < 0) {
                        break;
                    }
                    low = low * 16 + d;
                }
                if (k == 7 && low >= 0xDC00 && low <= 0xDFFF) {
                    code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00);
                    ps->p += 6;
                }
            }
            w = put_utf8(w, code);
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

int sb_add(sbuf *b, const char *s, size_t n)
{
    if (b->len + n + 1 > b->cap) {
        size_t cap = (b->cap == 0) ? 256 : b->cap;
        char *grown;
        while (b->len + n + 1 > cap) {
            cap *= 2;
        }
        grown = realloc(b->data, cap);
        if (grown == NULL) {
            return -1;
        }
        b->data = grown;
        b->cap = cap;
    }
    memcpy(b->data + b->len, s, n);
    b->len += n;
    b->data[b->len] = '\0';
    return 0;
}

int sb_adds(sbuf *b, const char *s)
{
    return sb_add(b, s, strlen(s));
}

void sb_free(sbuf *b)
{
    free(b->data);
    b->data = NULL;
    b->len = 0;
    b->cap = 0;
}

/* Decodes one UTF-8 sequence at s. Sets *cp and returns its length. Invalid bytes
   give U+FFFD and length 1. */
static size_t get_utf8(const unsigned char *s, size_t avail, long *cp)
{
    size_t need;
    long value;
    size_t i;

    if (s[0] < 0x80) {
        *cp = s[0];
        return 1;
    }
    if (s[0] >= 0xC2 && s[0] < 0xE0) {
        need = 1; value = s[0] & 0x1F;
    } else if (s[0] >= 0xE0 && s[0] < 0xF0) {
        need = 2; value = s[0] & 0x0F;
    } else if (s[0] >= 0xF0 && s[0] < 0xF5) {
        need = 3; value = s[0] & 0x07;
    } else {
        *cp = 0xFFFD;
        return 1;
    }
    if (avail < need + 1) {
        *cp = 0xFFFD;
        return 1;
    }
    for (i = 1; i <= need; i++) {
        if ((s[i] & 0xC0) != 0x80) {
            *cp = 0xFFFD;
            return 1;
        }
        value = (value << 6) | (s[i] & 0x3F);
    }
    *cp = value;
    return need + 1;
}

static int write_string(sbuf *b, const char *s, size_t len)
{
    const unsigned char *p = (const unsigned char *)s;
    char esc[32];
    size_t i = 0, n;
    long cp;

    if (sb_adds(b, "\"") < 0) {
        return -1;
    }
    while (i < len) {
        if (p[i] >= 0x80) {
            n = get_utf8(p + i, len - i, &cp);
            i += n;
            if (cp >= 0x10000) {
                long v = cp - 0x10000;
                sprintf(esc, "\\u%04lx\\u%04lx", 0xD800 + (v >> 10), 0xDC00 + (v & 0x3FF));
            } else {
                sprintf(esc, "\\u%04lx", cp);
            }
            if (sb_adds(b, esc) < 0) return -1;
            continue;
        }
        i++;
        switch (p[i - 1]) {
        case '"': if (sb_adds(b, "\\\"") < 0) return -1; break;
        case '\\': if (sb_adds(b, "\\\\") < 0) return -1; break;
        case '\n': if (sb_adds(b, "\\n") < 0) return -1; break;
        case '\r': if (sb_adds(b, "\\r") < 0) return -1; break;
        case '\t': if (sb_adds(b, "\\t") < 0) return -1; break;
        case '\b': if (sb_adds(b, "\\b") < 0) return -1; break;
        case '\f': if (sb_adds(b, "\\f") < 0) return -1; break;
        default:
            if (p[i - 1] < 0x20) {
                sprintf(esc, "\\u%04x", (unsigned)p[i - 1]);
                if (sb_adds(b, esc) < 0) return -1;
            } else if (sb_add(b, (const char *)p + i - 1, 1) < 0) {
                return -1;
            }
        }
    }
    return sb_adds(b, "\"");
}

int json_write_string(sbuf *b, const char *s)
{
    return write_string(b, s, strlen(s));
}

int json_write(sbuf *b, const jval *v)
{
    size_t i;
    char num[40];

    switch (v->type) {
    case J_NULL:
        return sb_adds(b, "null");
    case J_BOOL:
        return sb_adds(b, v->boolean ? "true" : "false");
    case J_NUM:
        if (v->num - v->num != 0.0) {
            return sb_adds(b, "null");          /* not finite */
        }
        if (v->num == (double)(long)v->num && v->num > -1e15 && v->num < 1e15) {
            sprintf(num, "%ld", (long)v->num);
        } else {
            sprintf(num, "%.17g", v->num);
        }
        return sb_adds(b, num);
    case J_STR:
        return write_string(b, v->str, strlen(v->str));
    case J_ARR:
        if (sb_adds(b, "[") < 0) return -1;
        for (i = 0; i < v->n; i++) {
            if (i > 0 && sb_adds(b, ", ") < 0) return -1;
            if (json_write(b, v->items[i]) < 0) return -1;
        }
        return sb_adds(b, "]");
    case J_OBJ:
        if (sb_adds(b, "{") < 0) return -1;
        for (i = 0; i < v->n; i++) {
            if (i > 0 && sb_adds(b, ", ") < 0) return -1;
            if (write_string(b, v->keys[i], strlen(v->keys[i])) < 0) return -1;
            if (sb_adds(b, ": ") < 0) return -1;
            if (json_write(b, v->items[i]) < 0) return -1;
        }
        return sb_adds(b, "}");
    }
    return -1;
}
