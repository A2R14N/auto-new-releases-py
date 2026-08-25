import http.client
import unittest

from anr.bridge_api import BridgeAPI
from anr.bridge_server import BridgeServer


class BridgeSafetyTests(unittest.TestCase):
    def test_timed_out_request_is_cancelled(self):
        server = BridgeServer(port=0)

        with self.assertRaises(TimeoutError):
            server.call("never_handled", timeout=0.01)

        request = server._request_queue.get_nowait()
        self.assertTrue(request.cancelled)

    def test_bridge_requires_session_token_and_rejects_web_origins(self):
        server = BridgeServer(port=0)
        server.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=2)
            connection.request("GET", "/status")
            response = connection.getresponse()
            session = __import__("json").loads(response.read())
            self.assertEqual(200, response.status)

            spotify_origin = "https://xpui.app.spotify.com"
            connection.request("GET", "/status", headers={"Origin": spotify_origin})
            response = connection.getresponse()
            response.read()
            self.assertEqual(200, response.status)
            self.assertEqual(spotify_origin, response.getheader("Access-Control-Allow-Origin"))

            connection.request("GET", "/request")
            response = connection.getresponse()
            response.read()
            self.assertEqual(401, response.status)

            connection.request(
                "GET",
                "/request",
                headers={
                    "Origin": spotify_origin,
                    "X-ANR-Token": session["session_token"],
                },
            )
            response = connection.getresponse()
            response.read()
            self.assertEqual(204, response.status)

            connection.request(
                "GET", "/status", headers={"Origin": "https://example.com"}
            )
            response = connection.getresponse()
            response.read()
            self.assertEqual(403, response.status)
        finally:
            connection.close()
            server.stop()

    def test_false_bridge_write_response_is_not_success(self):
        api = object.__new__(BridgeAPI)
        api._call = lambda *_args, **_kwargs: {"success": False}

        self.assertFalse(api.add_tracks_to_playlist("playlist", ["spotify:track:x"]))
        self.assertFalse(api.remove_playlist_tracks("playlist", ["spotify:track:x"]))
        self.assertFalse(api.replace_playlist_tracks("playlist", ["spotify:track:x"]))


if __name__ == "__main__":
    unittest.main()
