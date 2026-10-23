import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from anr.models import Artist, Profile
from anr.profile import ProfileManager
from anr.ui import ApplicationUI, _parse_artist_selection


class ArtistRemovalTests(unittest.TestCase):
    def setUp(self):
        self.profile = Profile('test', 'Test', artists=[
            Artist(f'spotify:artist:{index}', f'Artist {index}') for index in range(1, 11)])
        self.profile.pending_artists = {artist.uri: {'error': 'pending'} for artist in self.profile.artists}
        self.profile.tracked_tracks = {'spotify:track:kept': 1}
        self.profile.tracked_releases = {'spotify:album:kept': 1}
        self.config = SimpleNamespace(get_active_profile=lambda: self.profile, save=Mock())
        self.manager = ProfileManager(self.config)
        self.ui = ApplicationUI(self.config, self.manager)

    def test_numbers_ranges_whitespace_and_duplicates(self):
        for value, expected in (
            ('1,2,3', [1, 2, 3]), ('1-5, 7-9', [1, 2, 3, 4, 5, 7, 8, 9]),
            (' 3 , 1 - 3, 2, 10 ', [1, 2, 3, 10]), ('5-5', [5]),
            ('0', []), (' ', []), ('10', [10]),
        ):
            with self.subTest(value=value):
                self.assertEqual(expected, _parse_artist_selection(value, 10))

    def test_invalid_selection_is_rejected_as_a_whole(self):
        for value in ('1,11', '1-11', '5-1', '0,1', '-1', '1,,2', '1,',
                      '1 2', 'a', '1-2-3', '1.5', '1-999999999999999'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _parse_artist_selection(value, 10)

    def test_removal_uses_original_numbers_and_saves_once(self):
        with patch.object(self.manager, 'display_artists'), \
                patch('anr.ui._prompt', return_value='1-3, 7-9, 2'), \
                patch('anr.ui._confirm', return_value=True) as confirm, \
                patch('anr.ui.print_info') as preview, patch('anr.profile.print_success'):
            self.ui._remove_artist()
        self.assertEqual(['Artist 4', 'Artist 5', 'Artist 6', 'Artist 10'],
                         [artist.name for artist in self.profile.artists])
        self.assertEqual({artist.uri for artist in self.profile.artists}, set(self.profile.pending_artists))
        self.assertEqual({'spotify:track:kept': 1}, self.profile.tracked_tracks)
        self.assertEqual({'spotify:album:kept': 1}, self.profile.tracked_releases)
        confirm.assert_called_once_with('Remove these 6 artists from tracking?')
        preview.assert_any_call('  [7] Artist 7')
        self.config.save.assert_called_once()

    def test_invalid_input_reprompts_without_removing_valid_parts(self):
        with patch.object(self.manager, 'display_artists'), \
                patch('anr.ui._prompt', side_effect=['1,11', '0']), \
                patch('anr.ui.print_error') as error, patch('anr.ui._confirm') as confirm:
            self.ui._remove_artist()
        self.assertEqual(10, len(self.profile.artists))
        self.config.save.assert_not_called()
        confirm.assert_not_called()
        error.assert_called_once()

    def test_declining_confirmation_preserves_all_artists(self):
        with patch.object(self.manager, 'display_artists'), \
                patch('anr.ui._prompt', return_value='1,2,3'), \
                patch('anr.ui._confirm', return_value=False), patch('anr.ui.print_info'):
            self.ui._remove_artist()
        self.assertEqual(10, len(self.profile.artists))
        self.assertEqual(10, len(self.profile.pending_artists))
        self.config.save.assert_not_called()

    def test_unknown_uri_prevents_partial_bulk_removal(self):
        with patch('anr.profile.print_error'):
            self.assertEqual(0, self.manager.remove_artists_from_profile(
                self.profile, ['spotify:artist:1', 'spotify:artist:unknown']))
        self.assertEqual(10, len(self.profile.artists))
        self.config.save.assert_not_called()

    def test_single_artist_removal_retains_cli_behavior(self):
        with patch('anr.profile.print_success') as success, patch('anr.profile.print_error'):
            self.assertTrue(self.manager.remove_artist_from_profile(self.profile, 'spotify:artist:1'))
            self.assertFalse(self.manager.remove_artist_from_profile(self.profile, 'spotify:artist:missing'))
        success.assert_called_once_with("Removed 'Artist 1' from tracking")
        self.config.save.assert_called_once()


if __name__ == '__main__':
    unittest.main()
