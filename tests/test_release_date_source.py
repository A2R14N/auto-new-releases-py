"""Playlist dates can differ from the original album dates Spotify displays."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from anr.bridge_api import BridgeAPI
from anr.playlist import PlaylistTrack
from anr.tools import PlaylistSorter


class ReleaseDateSourceTests(unittest.TestCase):
    def setup_sort(self, dates):
        api = BridgeAPI(Mock())
        api.get_album_release_dates = Mock(return_value=dates)
        old = PlaylistTrack('spotify:track:old', 'O Sa Platesti', ['Artist'], 'Old', 'spotify:album:old', '2026-10-01', uid='old-row')
        new = PlaylistTrack('spotify:track:new', 'New song', ['Artist'], 'New', 'spotify:album:new', '2026-09-25', uid='new-row')
        ops = SimpleNamespace(get_playlist_details=Mock(return_value={'name':'Test'}), get_playlist_tracks=Mock(return_value=[old,new]), reorder_tracks=Mock(return_value=True))
        return api, ops, PlaylistSorter(api, ops)

    def test_original_album_date_overrides_recent_playlist_row_date(self):
        api, ops, sorter = self.setup_sort({'spotify:album:old':'2021-02-09', 'spotify:album:new':'2026-09-25'})
        result = sorter.sort_playlist('spotify:playlist:p', create_backup=False)
        self.assertTrue(result.success, result.error_message)
        api.get_album_release_dates.assert_called_once_with(['spotify:album:old','spotify:album:new'], playlist_uri='spotify:playlist:p')
        saved = ops.reorder_tracks.call_args.args[2]
        self.assertEqual(['spotify:track:new','spotify:track:old'], [t['uri'] for t in saved])
        self.assertEqual(['new-row','old-row'], [t['uid'] for t in saved])

    def test_unknown_album_never_falls_back_to_misleading_row_date(self):
        _, ops, sorter = self.setup_sort({'spotify:album:new':'2026-09-25'})
        self.assertTrue(sorter.sort_playlist('spotify:playlist:p', create_backup=False).success)
        saved = ops.reorder_tracks.call_args.args[2]
        self.assertEqual('spotify:track:old', saved[-1]['uri'])
        self.assertEqual('', saved[-1]['release_date'])

    def test_date_lookup_failure_does_not_write_an_unverified_order(self):
        api, ops, sorter = self.setup_sort({})
        api.get_album_release_dates.side_effect = RuntimeError('Album lookup failed')
        result = sorter.sort_playlist('spotify:playlist:p', create_backup=False)
        self.assertFalse(result.success)
        self.assertIn('playlist unchanged', result.error_message)
        ops.reorder_tracks.assert_not_called()

    def test_cached_dates_keep_repeated_sorts_free_of_album_requests(self):
        api, ops, sorter = self.setup_sort({})
        del api.get_album_release_dates
        with tempfile.TemporaryDirectory() as temp, patch('anr.constants.CONFIG_DIR', Path(temp)):
            (Path(temp)/'album_dates_cache.json').write_text(json.dumps({'old':'2021-02-09','new':'2026-09-25'}),encoding='utf-8')
            for _ in range(2):
                self.assertTrue(sorter.sort_playlist('spotify:playlist:p', create_backup=False).success)
            api._server.call.assert_not_called()
            self.assertEqual(2, ops.reorder_tracks.call_count)


if __name__ == '__main__':
    unittest.main()
