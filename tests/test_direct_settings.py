import io
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rich.console import Console
from rich.theme import Theme
from rich.cells import cell_len

from anr.config import ConfigManager
from anr.constants import CONSOLE_STYLES
from anr.models import Artist, Config, Profile
from anr.profile import ProfileManager, ProfileMenu
from anr.settings import SETTINGS, SETTING_BY_KEY
from anr.ui import ApplicationUI
from anr import views, output


class DirectSettingsTests(unittest.TestCase):
    def setUp(self):
        self.profile = Profile('one', 'POP RO', artists=[Artist('a', 'Artist')],
            limit_songs_per_album=True, max_songs_per_album=5,
            skip_long_albums=True, max_songs=24,
            skip_low_popularity=True, min_popularity=35,
            playlist_uri='spotify:playlist:kept')
        self.other = Profile('two', 'Other')
        self.config = SimpleNamespace(config=Config(profiles=[self.profile, self.other],
            active_profile_id='one'), get_active_profile=lambda: self.profile, save=Mock())
        self.pm = ProfileManager(self.config)
        self.ui = ApplicationUI(self.config, self.pm)

    def drive(self, choices, prompts=()):
        with ExitStack() as stack:
            for name in ('clear_screen', 'display_settings', 'display_setting_editor',
                         'display_tracking_history', 'print_success', 'print_error', 'print_info'):
                stack.enter_context(patch('anr.ui.' + name))
            stack.enter_context(patch('anr.ui._choice', side_effect=choices))
            stack.enter_context(patch('anr.ui._prompt', side_effect=prompts))
            self.ui._menu_settings()

    def test_opening_and_cancelling_never_saves_or_changes_existing_settings(self):
        before = self.config.config.to_dict()
        self.drive(['6', 'c', '0', '0'], [''])
        self.assertEqual(before, self.config.config.to_dict())
        self.config.save.assert_not_called()

    def test_zero_disables_combined_filters_without_losing_their_saved_limits(self):
        for key, flag, field, old in (
            ('6', 'limit_songs_per_album', 'max_songs_per_album', 5),
            ('7', 'skip_long_albums', 'max_songs', 24),
            ('8', 'skip_low_popularity', 'min_popularity', 35),
        ):
            with self.subTest(key=key):
                self.drive([key, 'c', '0'], ['0'])
                self.assertFalse(getattr(self.profile, flag))
                self.assertEqual(old, getattr(self.profile, field))
                self.assertEqual(0, SETTING_BY_KEY[key].current(self.profile, self.config.config))
        self.assertEqual(3, self.config.save.call_count)
        self.assertEqual(Profile('two', 'Other'), self.other)

    def test_all_choices_reach_checker_without_ranking_or_size_restrictions(self):
        from test_recovery import fixture, song
        from anr.checker import CheckStatus

        checker, api, ops, profile, manager = fixture(1, limit_songs_per_album=True,
            max_songs_per_album=2, skip_long_albums=True, max_songs=3,
            skip_low_popularity=True, min_popularity=50)
        api.albums['spotify:album:a0'] = [song(str(index)) for index in range(7)]
        for key in ('6', '7', '8'):
            SETTING_BY_KEY[key].apply(profile, manager.config, 0)
        result = checker.check_profile(profile, silent=True)
        self.assertEqual(CheckStatus.SUCCESS, result.status)
        self.assertEqual(7, result.total_tracks_added)
        self.assertEqual(7, len(ops.rows))
        self.assertFalse(any(kind == 'tracks' for kind, _ in api.calls))

    def test_positive_custom_values_enable_filters_and_validate_before_saving(self):
        self.profile.limit_songs_per_album = False
        self.drive(['6', 'c', '0'], ['-1', '51', 'no', '2.5', '4'])
        self.assertTrue(self.profile.limit_songs_per_album)
        self.assertEqual(4, self.profile.max_songs_per_album)
        self.config.save.assert_called_once()

    def test_all_settings_have_working_named_choices_and_global_scope_is_explicit(self):
        expected = {'1': ('check_interval', 12), '2': ('days_to_check', 30),
                    '3': ('sort_by_date', False), '4': ('skip_remixes', True),
                    '5': ('skip_similar_duplicates', True), '6': ('max_songs_per_album', 3),
                    '7': ('max_songs', 20), '8': ('min_popularity', 30)}
        for key, (field, value) in expected.items():
            with self.subTest(key=key):
                self.drive([key, '2', '0'])
                self.assertEqual(value, getattr(self.profile, field))
        self.drive(['9', '2', '0'])
        self.assertTrue(self.config.config.spotify_notifications)
        self.assertNotIn('spotify_notifications', self.profile.to_dict())
        self.assertEqual('On · all profiles', SETTING_BY_KEY['9'].format(True))
        self.assertEqual(Profile('two', 'Other'), self.other)

    def test_release_window_zero_and_interval_boundaries(self):
        self.drive(['2', '4', '1', 'c', '0'], ['0', '169', '168'])
        self.assertEqual(0, self.profile.days_to_check)
        self.assertEqual(168, self.profile.check_interval)
        self.assertEqual(2, self.config.save.call_count)

    def test_cancelling_custom_entry_with_b_returns_to_editor(self):
        self.drive(['6', 'c', '0', '0'], ['b'])
        self.assertTrue(self.profile.limit_songs_per_album)
        self.config.save.assert_not_called()

    def test_save_failure_restores_profile_or_global_values(self):
        self.config.save.side_effect = OSError('Disk full')
        before = self.config.config.to_dict()
        self.assertEqual('error', self.ui._save_setting(SETTING_BY_KEY['6'], 3)[1])
        self.assertEqual('error', self.ui._save_setting(SETTING_BY_KEY['9'], True)[1])
        self.assertEqual(before, self.config.config.to_dict())

    def test_history_requires_exact_confirmation_and_keeps_playlist_and_preferences(self):
        self.profile.tracked_releases = {'release': 1}
        self.profile.tracked_tracks = {'track': 1}
        self.profile.pending_artists = {'a': {'error': 'pending'}}
        self.profile.last_scan = 123
        self.profile.pending_sort = True
        self.drive(['h', 'c', '0', '0'], ['clear', ''])
        self.config.save.assert_not_called()
        self.assertEqual({'track': 1}, self.profile.tracked_tracks)
        with patch('anr.profile.print_success'):
            self.drive(['h', 'c', '0', '0'], ['CLEAR'])
        self.config.save.assert_called_once()
        self.assertEqual({}, self.profile.tracked_releases)
        self.assertEqual({}, self.profile.tracked_tracks)
        self.assertEqual({}, self.profile.pending_artists)
        self.assertIsNone(self.profile.last_scan)
        self.assertEqual('spotify:playlist:kept', self.profile.playlist_uri)
        self.assertEqual([Artist('a', 'Artist')], self.profile.artists)
        self.assertTrue(self.profile.pending_sort)
        self.assertEqual(5, self.profile.max_songs_per_album)

    def test_pending_only_history_can_be_cleared(self):
        self.profile.pending_artists = {'a': {'error': 'pending'}}
        with patch('anr.profile.print_success'):
            self.drive(['h', 'c', '0', '0'], ['CLEAR'])
        self.assertEqual({}, self.profile.pending_artists)
        self.config.save.assert_called_once()

    def test_history_save_failure_keeps_original_recovery_state(self):
        self.profile.pending_artists = {'a': {'error': 'pending'}}
        self.profile.tracked_releases = {'release': 1}
        self.profile.tracked_tracks = {'track': 1}
        self.profile.last_scan = 123
        before = self.profile.to_dict()
        self.config.save.side_effect = OSError('Disk full')
        self.drive(['h', 'c', '0', '0'], ['CLEAR'])
        self.assertEqual(before, self.profile.to_dict())

    def test_settings_persist_using_existing_config_format(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'config.json'
            manager = ConfigManager.__new__(ConfigManager)
            manager.config = self.config.config
            self.config.save = manager.save
            with patch('anr.config.CONFIG_FILE', target):
                self.drive(['6', 'c', '9', '2', '0'], ['0'])
            restored = Config.from_dict(json.loads(target.read_text('utf-8')))
        self.assertFalse(restored.profiles[0].limit_songs_per_album)
        self.assertEqual(5, restored.profiles[0].max_songs_per_album)
        self.assertTrue(restored.spotify_notifications)

    def test_legacy_interactive_entry_uses_direct_settings(self):
        with patch.object(ApplicationUI, '_menu_settings') as menu:
            ProfileMenu(self.pm).run_edit_settings()
        menu.assert_called_once()

    def test_rich_and_plain_screens_show_effective_values_and_safe_back_keys(self):
        self.profile.limit_songs_per_album = False
        for width in (68, 40, 32):
            screen = Console(file=io.StringIO(), width=width, record=True,
                             theme=Theme(CONSOLE_STYLES), highlight=False)
            with patch.object(views, 'console', screen), patch.object(output, 'console', screen):
                views.display_settings(self.profile, False)
                home = screen.export_text()
                self.assertIn('All songs', home)
                self.assertIn('[9]', home)
                for setting in SETTINGS:
                    views.display_setting_editor(setting, self.profile, self.config.config)
                    if setting.minimum is not None:
                        views.display_setting_editor(setting, self.profile, self.config.config, custom=True)
                        self.assertIn('[b] Back', screen.export_text())
                views.display_tracking_history(self.profile, confirm=True)
                self.assertTrue(all(cell_len(line) <= width for line in screen.export_text().splitlines()))
        with patch('anr.views.RICH_AVAILABLE', False), patch('sys.stdout', new_callable=io.StringIO) as captured:
            views.display_settings(self.profile, False)
            views.display_setting_editor(SETTING_BY_KEY['6'], self.profile, self.config.config, custom=True)
        self.assertIn('All songs', captured.getvalue())
        self.assertIn('[b] Back', captured.getvalue())


if __name__ == '__main__':
    unittest.main()
