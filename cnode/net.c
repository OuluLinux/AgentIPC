/* Loopback TCP listener for direct links. POSIX sockets. */
#define _POSIX_C_SOURCE 200112L

#include <netinet/in.h>
#include <signal.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "net.h"

int cn_listen_loopback(int port)
{
    int fd, on = 1;
    struct sockaddr_in sa;

    fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, (const char *)&on, sizeof on);
    memset(&sa, 0, sizeof sa);
    sa.sin_family = AF_INET;
    sa.sin_port = htons((unsigned short)port);
    sa.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(fd, (struct sockaddr *)&sa, sizeof sa) < 0 || listen(fd, 4) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

int cn_accept(int lfd, FILE **in, FILE **out)
{
    int fd, out_fd;

    *in = NULL;
    *out = NULL;
    fd = accept(lfd, NULL, NULL);
    if (fd < 0) {
        return -1;
    }
    out_fd = dup(fd);
    if (out_fd < 0) {
        close(fd);
        return -1;
    }
    *in = fdopen(fd, "r");
    *out = fdopen(out_fd, "w");
    if (*in == NULL || *out == NULL) {
        if (*in != NULL) {
            fclose(*in);
        } else {
            close(fd);
        }
        if (*out != NULL) {
            fclose(*out);
        } else {
            close(out_fd);
        }
        *in = NULL;
        *out = NULL;
        return -1;
    }
    return 0;
}

void cn_close(FILE *in, FILE *out)
{
    fclose(in);
    fclose(out);
}

void cn_ignore_sigpipe(void)
{
    signal(SIGPIPE, SIG_IGN);
}
