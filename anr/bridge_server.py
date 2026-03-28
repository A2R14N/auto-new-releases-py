"""
ANR Bridge Server
~~~~~~~~~~~~~~~~~
A lightweight threaded HTTP server that acts as a message bus between the
Python ANR app and the Spicetify browser extension running inside Spotify.

Architecture:
    Python app  ──►  BridgeServer  ◄──  Spicetify extension (polls)
                     (port 7421)

Endpoints:
    GET  /status    Health check — also used by extension to signal it is alive
    GET  /request   Pop and return the next pending request (or 204 if empty)
    POST /response  Extension posts the result here; wakes the waiting caller
"""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from queue import Empty, Queue
from typing import Any, Dict, Optional

BRIDGE_PORT = 7421
REQUEST_TIMEOUT = 30  # seconds to wait for extension to fulfil a request


# Internal message types
class _PendingRequest:
    """A request waiting to be picked up by the extension."""

    def __init__(self, method: str, params: Dict[str, Any]):
        self.id = str(uuid.uuid4())
        self.method = method
        self.params = params
        self._event = threading.Event()
        self._result: Any = None
        self._error: Optional[str] = None

    def wait(self, timeout: float = REQUEST_TIMEOUT) -> Any:
        """Block until the extension posts a response, then return the result."""
        if not self._event.wait(timeout=timeout):
            raise TimeoutError(
                f"Bridge request '{self.method}' timed out after {timeout}s. "
                "Is Spotify open with the ANR Bridge extension loaded?"
            )
        if self._error:
            raise RuntimeError(f"Bridge error in '{self.method}': {self._error}")
        return self._result

    def resolve(self, result: Any, error: Optional[str]):
        self._result = result
        self._error = error
        self._event.set()

    def to_dict(self) -> Dict:
        return {"id": self.id, "method": self.method, "params": self.params}


# HTTP request handler
class _BridgeHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler — delegates to BridgeServer state."""

    # Suppress default access-log noise
    def log_message(self, fmt, *args):
        pass

    # ------- helpers --------------------------------------------------------

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status: int = 204):
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ------- routing --------------------------------------------------------

    def do_OPTIONS(self):
        """CORS pre-flight — Spotify's browser context sends this."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        srv: BridgeServer = self.server._bridge  # type: ignore[attr-defined]

        if self.path == "/status":
            self._send_json({"status": "running", "connected": srv.extension_connected})

        elif self.path == "/request":
            # Signal that extension is alive
            srv._mark_extension_alive()

            try:
                # Non-blocking pop from the request queue
                req: _PendingRequest = srv._request_queue.get_nowait()
                srv._pending[req.id] = req
                self._send_json(req.to_dict())
            except Empty:
                self._send_empty(204)

        else:
            self._send_empty(404)

    def do_POST(self):
        srv: BridgeServer = self.server._bridge  # type: ignore[attr-defined]

        if self.path == "/response":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                req_id = data.get("id")
                if req_id and req_id in srv._pending:
                    req = srv._pending.pop(req_id)
                    req.resolve(data.get("result"), data.get("error"))
                self._send_empty(200)
            except Exception as e:
                self._send_json({"error": str(e)}, 400)
        else:
            self._send_empty(404)


# Public BridgeServer class
class BridgeServer:
    """
    Manages the local HTTP server and the request/response queues.

    Usage as a context manager (recommended)::

        with BridgeServer() as srv:
            api = BridgeAPI(srv)
            results = api.get_current_user()

    Or manually::

        srv = BridgeServer()
        srv.start()
        ...
        srv.stop()
    """

    def __init__(self, port: int = BRIDGE_PORT):
        self.port = port
        self._request_queue: Queue[_PendingRequest] = Queue()
        self._pending: Dict[str, _PendingRequest] = {}
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._last_ping = 0.0
        self._lock = threading.Lock()

    # Lifecycle
    def start(self):
        """Start the HTTP server on a daemon thread."""
        if self._server is not None:
            return  # already running

        self._server = HTTPServer(("127.0.0.1", self.port), _BridgeHandler)
        self._server._bridge = self  # back-reference for the handler

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="ANRBridgeServer",
        )
        self._thread.start()

    def stop(self):
        """Shut down the HTTP server."""
        if self._server:
            self._server.shutdown()
            self._server = None
        self._thread = None

    def __enter__(self) -> "BridgeServer":
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    # Status
    @property
    def extension_connected(self) -> bool:
        """True if the Spicetify extension has polled us in the last 5 seconds."""
        import time
        return (time.time() - self._last_ping) < 5.0

    def _mark_extension_alive(self):
        import time
        self._last_ping = time.time()

    def is_running(self) -> bool:
        return self._server is not None

    # Request dispatch
    def call(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = REQUEST_TIMEOUT,
    ) -> Any:
        """
        Enqueue a request and block until the Spicetify extension responds.

        Raises:
            TimeoutError: If the extension doesn't respond within *timeout* seconds.
            RuntimeError: If the extension returns an error.
        """
        req = _PendingRequest(method, params or {})
        self._request_queue.put(req)
        return req.wait(timeout=timeout)
