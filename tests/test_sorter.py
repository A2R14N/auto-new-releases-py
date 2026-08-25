import unittest
from unittest.mock import patch

from anr.playlist import PlaylistTrack
from anr.tools import PlaylistSorter


class FakeSortOps:
    def __init__(self):
        self.replacement = None

    def get_playlist_details(self, _uri):
        return {"name": "Test"}

    def get_playlist_tracks(self, _uri):
        return [
            PlaylistTrack(
                uri="spotify:track:old",
                name="Old",
                artists=["Artist"],
                album_name="Old album",
                album_uri="spotify:album:old",
                release_date="2025-01-01",
            ),
            PlaylistTrack(
                uri="spotify:track:new",
                name="New",
                artists=["Artist"],
                album_name="New album",
                album_uri="spotify:album:new",
                release_date="2026-01-01",
            ),
        ]

    def replace_all_tracks(self, _uri, track_uris, _callback):
        self.replacement = list(track_uris)
        return True


class PlaylistSorterTests(unittest.TestCase):
    def test_bridge_sort_progress_path_has_batch_limit_and_preserves_order(self):
        ops = FakeSortOps()
        sorter = PlaylistSorter(object(), ops)

        with patch("anr.tools._is_bridge", return_value=True):
            result = sorter.sort_playlist(
                "spotify:playlist:test", create_backup=False
            )

        self.assertTrue(result.success, result.error_message)
        self.assertEqual(
            ["spotify:track:new", "spotify:track:old"], ops.replacement
        )


if __name__ == "__main__":
    unittest.main()
