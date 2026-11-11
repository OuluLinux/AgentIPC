/* Checks the JSON reader on frames and on inputs it must reject. */
#include <stdio.h>
#include <string.h>

#include "json.h"

static int failures = 0;

static void expect_ok(const char *text)
{
    jval *v = json_parse(text, strlen(text));
    if (v == NULL) {
        printf("FAIL should parse: %s\n", text);
        failures++;
    }
    json_free(v);
}

static void expect_bad(const char *text)
{
    jval *v = json_parse(text, strlen(text));
    if (v != NULL) {
        printf("FAIL should reject: %s\n", text);
        failures++;
    }
    json_free(v);
}

int main(void)
{
    jval *v;
    const char *s;

    expect_ok("{\"type\": \"hello\", \"version\": 2, \"features\": [\"core\", \"vs\"]}");
    expect_ok("  [1, -2.5e3, true, false, null, \"a\\\"b\\u0041\"]  ");
    expect_ok("{}");
    expect_bad("");
    expect_bad("{");
    expect_bad("{\"a\" 1}");
    expect_bad("[1,]");
    expect_bad("{\"a\": 1} trailing");
    expect_bad("\"unterminated");
    expect_bad("\"bad \\x escape\"");
    expect_bad("01x");
    expect_bad("tru");

    s = "{\"node\": \"gen7\", \"version\": 2}";
    v = json_parse(s, strlen(s));
    s = (json_get(v, "node") != NULL) ? json_get(v, "node")->str : NULL;
    if (s == NULL || strcmp(s, "gen7") != 0) {
        printf("FAIL string lookup\n");
        failures++;
    }
    if (json_get(v, "missing") != NULL) {
        printf("FAIL missing key should be NULL\n");
        failures++;
    }
    json_free(v);

    /* \u escapes decode to UTF-8, surrogate pairs included. */
    s = "\"caf\\u00e9 \\ud83d\\ude00\"";
    v = json_parse(s, strlen(s));
    if (v == NULL || strcmp(v->str, "caf\xc3\xa9 \xf0\x9f\x98\x80") != 0) {
        printf("FAIL \\u decoding\n");
        failures++;
    }
    json_free(v);

    /* The writer escapes non-ASCII as \\u and round-trips through the reader. */
    s = "{\"b\": [1, 2.5, true, null], \"a\": \"x\\ny\xc3\xa9\"}";
    v = json_parse(s, strlen(s));
    if (v == NULL) {
        printf("FAIL writer input\n");
        failures++;
    } else {
        sbuf out = {NULL, 0, 0};
        jval *again;
        if (json_write(&out, v) < 0) {
            printf("FAIL writer\n");
            failures++;
        } else {
            size_t i;
            for (i = 0; i < out.len; i++) {
                if ((unsigned char)out.data[i] >= 0x80) {
                    printf("FAIL writer output not ASCII\n");
                    failures++;
                    break;
                }
            }
            again = json_parse(out.data, out.len);
            if (again == NULL || strcmp(json_get(again, "a")->str, "x\ny\xc3\xa9") != 0 ||
                json_get(again, "b")->n != 4) {
                printf("FAIL writer round trip: %s\n", out.data);
                failures++;
            }
            json_free(again);
        }
        sb_free(&out);
    }
    json_free(v);

    if (failures == 0) {
        printf("json ok\n");
        return 0;
    }
    return 1;
}
