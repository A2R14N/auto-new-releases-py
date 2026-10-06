import http.client
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from anr.bridge_server import BridgeServer


class LongPollTests(unittest.TestCase):
    def request(self, server, method, path, data=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
        try:
            connection.request(method, path, body=json.dumps(data) if data is not None else None,
                               headers=headers or {"X-ANR-Token": server.session_token})
            response = connection.getresponse()
            body = response.read()
            return response.status, json.loads(body) if body else None
        finally:
            connection.close()

    def test_waiting_poll_wakes_on_work_and_does_not_block_other_endpoints(self):
        with BridgeServer(port=0) as server, ThreadPoolExecutor(max_workers=3) as pool:
            started = threading.Event()
            mark = server._mark_extension_alive

            def mark_started():
                mark()
                started.set()

            server._mark_extension_alive = mark_started
            poll = pool.submit(self.request, server, "GET", "/request?wait_ms=500")
            self.assertTrue(started.wait(1))
            self.assertEqual(200, self.request(server, "GET", "/status")[0])
            self.assertFalse(poll.done(), "status must respond while a poll is waiting")
            call = pool.submit(server.call, "read", {"test": True}, 2)
            status, request = poll.result(2)
            self.assertEqual(200, status)
            self.assertEqual("read", request["method"])
            posts = [pool.submit(self.request, server, "POST", "/response",
                                 {"id": request["id"], "result": "done", "error": None})
                     for _ in range(2)]
            self.assertEqual([200, 200], [post.result(2)[0] for post in posts])
            self.assertEqual("done", call.result(2))
            self.assertEqual({}, server._pending)

    def test_long_poll_keeps_authentication_and_origin_checks(self):
        with BridgeServer(port=0) as server:
            self.assertEqual(401, self.request(server, "GET", "/request?wait_ms=500",
                                              headers={"X-ANR-Token": "wrong"})[0])
            self.assertEqual(403, self.request(server, "GET", "/request?wait_ms=500",
                                              headers={"X-ANR-Token": server.session_token,
                                                       "Origin": "https://example.com"})[0])

    def test_long_poll_skips_cancelled_requests_and_bounds_wait_time(self):
        with BridgeServer(port=0) as server:
            with self.assertRaises(TimeoutError):
                server.call("cancelled", timeout=0.01)
            start = time.monotonic()
            self.assertEqual(204, self.request(server, "GET", "/request?wait_ms=999999")[0])
            self.assertLess(time.monotonic() - start, 1.5)
            self.assertEqual({}, server._pending)
            self.assertEqual(204, self.request(server, "GET", "/request?wait_ms=invalid")[0])


if __name__ == "__main__":
    unittest.main()
