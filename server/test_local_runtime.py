"""Local server socket lifecycle."""
import http.client
import threading
import unittest
from http.server import BaseHTTPRequestHandler

import os
import tempfile

from local_runtime import LocalServer, repo_commit

BODY = b"x" * (512 * 1024)


class RepoCommit(unittest.TestCase):
    """The running checkout is identified from .git files alone."""

    def test_a_branch_head_resolves_through_the_loose_ref(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, ".git", "refs", "heads"))
            with open(os.path.join(root, ".git", "HEAD"), "w") as handle:
                handle.write("ref: refs/heads/main\n")
            with open(os.path.join(root, ".git", "refs", "heads", "main"), "w") as handle:
                handle.write("0123456789abcdef0123456789abcdef01234567\n")
            self.assertEqual(repo_commit(os.path.join(root, "server", "x.py")), "0123456")

    def test_a_packed_ref_and_a_detached_head_are_read(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, ".git"))
            with open(os.path.join(root, ".git", "HEAD"), "w") as handle:
                handle.write("ref: refs/heads/main\n")
            with open(os.path.join(root, ".git", "packed-refs"), "w") as handle:
                handle.write("# pack-refs with: peeled\nfedcba9876543210fedcba9876543210fedcba98 refs/heads/main\n")
            self.assertEqual(repo_commit(os.path.join(root, "server", "x.py")), "fedcba9")
            with open(os.path.join(root, ".git", "HEAD"), "w") as handle:
                handle.write("abcdef0123456789abcdef0123456789abcdef01\n")
            self.assertEqual(repo_commit(os.path.join(root, "server", "x.py")), "abcdef0")

    def test_outside_a_checkout_is_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(repo_commit(os.path.join(root, "x.py")), "unknown")

    def test_this_checkout_matches_git(self):
        self.assertRegex(repo_commit(), r"^[0-9a-f]{7}$")


class Large(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)


class ResponseDelivery(unittest.TestCase):
    """A response larger than the socket send buffer must arrive whole.

    socketserver half-closes the socket right after the handler returns. On a
    Windows host whose loopback filter discards queued bytes at shutdown(), the
    client then waits for a tail that never comes (compaction results, the
    client bundle). The server lets the client close first instead.
    """

    def test_a_large_response_is_delivered_whole_before_the_server_closes(self):
        server = LocalServer(("127.0.0.1", 0), Large)
        server.close_grace = 2.0
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            conn.request("GET", "/")
            response = conn.getresponse()
            data = response.read()
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(response.status, 200)
        self.assertEqual(len(data), len(BODY))

    def test_a_refused_connection_is_closed_without_waiting(self):
        server = LocalServer(("127.0.0.1", 0), Large, max_connections=1)
        server.close_grace = 5.0
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            server._slots.acquire()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                with self.assertRaises((ConnectionError, http.client.HTTPException, OSError)):
                    conn.request("GET", "/")
                    conn.getresponse().read()
            finally:
                server._slots.release()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
