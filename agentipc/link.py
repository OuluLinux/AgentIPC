# -*- coding: utf-8 -*-
"""One link to a neighbour node: a byte stream carrying proto frames.

Reading and writing each run in their own thread, with an outgoing queue, so
that a node never blocks on pipe I/O while it holds its lock.
"""

import threading
import traceback

from agentipc import proto

try:
    import queue
except ImportError:  # Python 2
    import Queue as queue


class Link(object):
    def __init__(self, rfile, wfile, kind, on_frame, on_close, closer=None):
        self.kind = kind          # "ssh", "direct" or "local"
        self.peer = None          # neighbour node id, set after hello
        self.version = None       # wire version negotiated at hello
        self.features = []        # what the neighbour says it can do
        self.rfile = rfile
        self.wfile = wfile
        self.on_frame = on_frame
        self.on_close = on_close
        self.closer = closer      # extra cleanup, e.g. kill ssh or shut a socket
        self.closed = False
        self.closed_event = threading.Event()
        self._out = queue.Queue()
        self._lock = threading.Lock()

    def start(self):
        for target, name in ((self._write_loop, "link-write"), (self._read_loop, "link-read")):
            thread = threading.Thread(target=target, name=name)
            thread.daemon = True
            thread.start()

    def send(self, frame):
        if not self.closed:
            self._out.put(frame)

    def close(self, reason=None):
        with self._lock:
            if self.closed:
                return
            self.closed = True
        if reason:
            self._out.put(proto.bye(reason))
        self._out.put(None)
        try:
            self.on_close(self)
        finally:
            if self.closer:
                try:
                    self.closer()
                except Exception:
                    pass
            self.closed_event.set()

    def _write_loop(self):
        while True:
            frame = self._out.get()
            if frame is None:
                break
            try:
                self.wfile.write(proto.encode(frame))
                self.wfile.flush()
            except (IOError, OSError, ValueError):
                break
        try:
            self.wfile.close()
        except (IOError, OSError, ValueError):
            pass
        self.close()

    def _read_loop(self):
        try:
            while True:
                line = self.rfile.readline(proto.MAX_LINE + 1)
                if not line:
                    break
                try:
                    frame = proto.decode(line)
                except proto.ProtoError as exc:
                    self.close(str(exc))
                    return
                try:
                    self.on_frame(self, frame)
                except Exception:
                    traceback.print_exc()
        except (IOError, OSError, ValueError):
            pass
        self.close()
