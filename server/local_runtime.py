"""Local service access controls and bounded, owner-only diagnostic files."""
import hmac
import os
import stat
import threading
from http.server import ThreadingHTTPServer

STATE = (os.environ.get("MODEL_ROUTER_STATE_DIR") or os.environ.get("CODEX_ROUTER_STATE_DIR")
         or os.environ.get("KIMI_CODEX_STATE_DIR")
         or os.path.join(os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex")), "codex-router"))
AUTH_PATH = os.path.join(STATE, "generic-provider-credentials", "jev.key")
MAX_LOG_BYTES = 8 * 1024 * 1024
_file_lock = threading.Lock()

if os.name == "nt":
    from windows_private import open_file, private, protect
else:
    def open_file(path, flags):
        return os.open(path, flags | os.O_NOFOLLOW, 0o600)

    def private(fd):
        info = os.fstat(fd)
        return not info.st_mode & 0o077 and info.st_uid == os.getuid()

    def protect(fd):
        os.fchmod(fd, 0o600)


def local_secret():
    """Use the parent's protected generic-provider credential; fail closed."""
    try:
        fd = open_file(AUTH_PATH, os.O_RDONLY)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or not private(handle.fileno()):
                return ""
            return handle.read(4096).strip()
    except (OSError, UnicodeError):
        return ""


def authorized(header, secret):
    return bool(secret) and hmac.compare_digest(
        str(header or "").encode(), ("Bearer " + secret).encode()
    )


def append_private(path, text, max_bytes=MAX_LOG_BYTES):
    """Bound retention to current + one backup. Never follow file symlinks."""
    data = text.encode("utf-8")
    if len(data) > max_bytes:
        data = data[:max_bytes].decode("utf-8", "ignore").encode()
    with _file_lock:
        fd = open_file(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        try:
            protect(fd)
            if os.fstat(fd).st_size + len(data) > max_bytes:
                backup = path + ".1"
                os.close(fd)
                fd = None
                os.replace(path, backup)
                fd = open_file(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(fd, "ab") as handle:
                fd = None
                handle.write(data)
        finally:
            if fd is not None:
                os.close(fd)


def protect_logs(paths):
    """Secure existing captures without reading or exposing their contents."""
    for path in paths:
        for candidate in (path, path + ".1"):
            try:
                fd = open_file(candidate, os.O_RDONLY)
                try:
                    protect(fd)
                finally:
                    os.close(fd)
            except OSError:
                pass


class LocalServer(ThreadingHTTPServer):
    """Bound concurrent connections, including clients waiting to send a body."""
    daemon_threads = True

    def __init__(self, *args, max_connections=32, **kwargs):
        self._slots = threading.BoundedSemaphore(max_connections)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()
