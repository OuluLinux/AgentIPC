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

    if (failures == 0) {
        printf("json ok\n");
        return 0;
    }
    return 1;
}
