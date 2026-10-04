# stream_server.py: serves the compositor's finished JPEG as an MJPEG stream over HTTP.

import argparse
import os
import socket
import socketserver
import sys
import threading
import time

BOUNDARY = b"ffmpeg"

STREAM_HEADERS = (
    b"HTTP/1.1 200 OK\r\n"
    b"Server: imgstream\r\n"
    b"Cache-Control: no-store, no-cache, must-revalidate\r\n"
    b"Pragma: no-cache\r\n"
    b"Content-Type: multipart/x-mixed-replace;boundary=" + BOUNDARY + b"\r\n"
    b"\r\n"
)

PART_BOUNDARY = b"--" + BOUNDARY + b"\r\n"
PART_TAIL = b"\r\n"

def part_head(length):
    return (PART_BOUNDARY
            + b"Content-Type: image/jpeg\r\n"
            + b"Content-Length: " + str(length).encode() + b"\r\n"
            + b"\r\n")

def log(msg):
    print("{} server: {}".format(
        time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)

def file_stamp(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)

def read_frame(frame_path, cache):
    stamp = file_stamp(frame_path)
    if stamp is None:
        return None
    if cache.get("stamp") != stamp:
        try:
            with open(frame_path, "rb") as fh:
                data = fh.read()
        except OSError:
            return cache.get("data")
        if data:
            cache["stamp"] = stamp
            cache["data"] = data
    return cache.get("data")

def serve_one(conn, frame_path, fps, stop):
    interval = 1.0 / fps
    cache = {}
    try:
        conn.sendall(STREAM_HEADERS)
        next_due = time.monotonic() + interval
        while not stop.is_set():
            data = read_frame(frame_path, cache)
            if data:
                conn.sendall(part_head(len(data)))
                conn.sendall(data)
                conn.sendall(PART_TAIL)

            next_due += interval
            now = time.monotonic()
            if next_due < now:
                next_due = now
            else:
                time.sleep(next_due - now)
    except (BrokenPipeError, ConnectionResetError):
        pass
    except OSError as exc:
        log("client error: {}".format(exc))
    finally:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()

class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        conn = self.request
        conn.settimeout(10)
        request = b""
        try:
            while b"\r\n\r\n" not in request:
                chunk = conn.recv(1024)
                if not chunk:
                    return
                request += chunk
                if len(request) > 16384:
                    return
        except (socket.timeout, OSError):
            return

        first_line = request.split(b"\r\n", 1)[0].upper()
        if not first_line.startswith(b"GET "):
            try:
                conn.sendall(b"HTTP/1.0 405 Method Not Allowed\r\n"
                             b"Content-Length: 0\r\n\r\n")
            except OSError:
                pass
            return

        log("client connected from {}".format(self.client_address[0]))
        serve_one(conn, self.server.frame_path, self.server.fps,
                  self.server.stop)

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("frame", nargs="?", default="/data/frame.jpg",
                    help="path to the compositor's output file")
    ap.add_argument("port", nargs="?", type=int, default=8084,
                    help="port to serve on")
    ap.add_argument("fps", nargs="?", type=float, default=2.0,
                    help="frames per second to send each client")
    args = ap.parse_args()

    if args.fps <= 0:
        log("fps must be above zero, using 2")
        args.fps = 2.0

    stop = threading.Event()
    httpd = Server(("0.0.0.0", args.port), Handler)
    httpd.frame_path = args.frame
    httpd.fps = args.fps
    httpd.stop = stop

    if not os.path.exists(args.frame):
        log("waiting for {} ...".format(args.frame))

    log("serving {} as MJPEG over HTTP on port {} at {:g} fps, "
        "any number of clients".format(args.frame, args.port, args.fps))

    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        log("shutting down")
        stop.set()
        httpd.shutdown()
    return 0

if __name__ == "__main__":
    sys.exit(main())
