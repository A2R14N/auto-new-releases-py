import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rich.console import Console
from rich.theme import Theme
from rich.cells import cell_len

from anr import checker, output, views
from anr.checker import (CheckStatus, InteractiveChecker, ProfileCheckResult,
                         ReleaseChecker, ScheduledChecker, check_profiles_with_progress)
from anr.cli import CLIHandler, create_argument_parser
from anr.config import ConfigManager
from anr.constants import CONSOLE_STYLES
from anr.importer import ProfileExporter, ProfileImporter
from anr.models import Artist, Config, Profile
from anr.profile import ProfileManager
from anr.ui import ApplicationUI


class ProfileEnabledTests(unittest.TestCase):
    def setUp(self):
        self.on = Profile('one', 'Enabled', playlist_uri='spotify:playlist:on',
                          artists=[Artist('a', 'Artist')])
        self.off = Profile('two', 'Disabled', playlist_uri='spotify:playlist:off',
                           artists=[Artist('b', 'Other')], enabled=False,
                           tracked_tracks={'track': 1}, tracked_releases={'album': 2},
                           pending_artists={'b': {'error': 'pending', 'retry_at': 0,
                                                 'playlist_uri': 'spotify:playlist:off'}},
                           pending_sort=True)
        self.config = SimpleNamespace(config=Config(profiles=[self.on, self.off], active_profile_id='two'),
            save=Mock(), get_active_profile=lambda: self.off,
            get_profile_by_id=lambda pid: next((p for p in (self.on, self.off) if p.id == pid), None))
        self.pm = ProfileManager(self.config)
        self.backend = Mock()
        self.backend.check_profile.side_effect = lambda p, **kw: ProfileCheckResult(p.name, CheckStatus.NO_NEW)

    def test_bulk_checks_and_dry_runs_omit_disabled_profiles_and_leave_them_untouched(self):
        before = self.off.to_dict()
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                self.backend.reset_mock()
                results = check_profiles_with_progress(self.backend, [self.on, self.off],
                                                       enabled=False, dry_run=dry_run)
                self.assertEqual(['Enabled'], [r.profile_name for r in results])
                self.backend.check_profile.assert_called_once_with(
                    self.on, progress_callback=None, silent=True, dry_run=dry_run)
        self.assertEqual(before, self.off.to_dict())

    def test_all_disabled_is_a_clear_noop_without_starting_a_live_display(self):
        with patch('anr.check_display.CheckAllDisplay') as display, patch('anr.checker.print_info') as info:
            self.assertEqual([], check_profiles_with_progress(self.backend, [self.off]))
        display.assert_not_called()
        self.backend.check_profile.assert_not_called()
        info.assert_called_once_with('No enabled profiles to check.')

    def test_interactive_confirmation_and_progress_only_include_enabled_profiles(self):
        runner = InteractiveChecker(Mock(), Mock(), self.config)
        with patch('anr.checker.print_profiles_summary') as summary, \
                patch('rich.prompt.Confirm.ask', return_value=True), \
                patch('anr.checker.check_profiles_with_progress', return_value=[]) as run:
            runner.run_check_all()
        summary.assert_called_once_with([self.on])
        run.assert_called_once_with(runner.checker, [self.on], interactive=True)

    def test_legacy_bulk_and_scheduled_recovery_also_skip_disabled_profiles(self):
        engine = ReleaseChecker(Mock(), Mock(), self.config)
        engine.check_profile = self.backend.check_profile
        engine.check_all_profiles(silent=True)
        self.assertEqual([self.on], [c.args[0] for c in self.backend.check_profile.call_args_list])
        scheduler = ScheduledChecker(Mock(), Mock(), self.config)
        self.assertEqual([self.on], scheduler.get_profiles_due())
        scheduler.checker = self.backend
        self.backend.reset_mock()
        scheduler.check_due_profiles()
        self.backend.check_profile.assert_called_once_with(self.on, silent=True, recovery_due_only=True)

    def test_explicit_manual_check_still_works_for_a_disabled_profile(self):
        app = SimpleNamespace(config_manager=self.config, profile_manager=self.pm,
                              release_checker=self.backend)
        with patch('anr.cli.print_success'), patch('anr.cli.print_info'):
            self.assertEqual(0, CLIHandler(app, quiet=True)._check_profile('disabled', dry_run=True))
        self.backend.check_profile.assert_called_once_with(self.off, silent=True, dry_run=True)
        self.assertFalse(self.off.enabled)

    def test_cli_enable_disable_and_interactive_toggle_persist_without_changing_active_profile(self):
        app = SimpleNamespace(config_manager=self.config, profile_manager=self.pm)
        parser = create_argument_parser()
        with patch('anr.profile.print_success'):
            self.assertEqual(0, CLIHandler(app).cmd_profile(parser.parse_args(['profile', 'enable', 'disabled'])))
            self.assertTrue(self.off.enabled)
            ui = ApplicationUI(self.config, self.pm)
            with patch('anr.ui.clear_screen'), patch('anr.ui.display_profiles_dashboard'), \
                    patch('anr.ui._choice', side_effect=['e', '0']):
                ui._menu_profiles()
            self.assertFalse(self.off.enabled)
        self.assertEqual(2, self.config.save.call_count)
        self.assertEqual('two', self.config.config.active_profile_id)
        self.assertEqual({'track': 1}, self.off.tracked_tracks)
        self.assertTrue(self.off.pending_sort)

    def test_toggle_save_failure_restores_status_and_cancellation_never_saves(self):
        self.config.save.side_effect = OSError('Disk full')
        with patch('anr.profile.print_error'):
            self.assertFalse(self.pm.set_profile_enabled('two', True))
        self.assertFalse(self.off.enabled)
        self.config.save.reset_mock()
        with patch('anr.ui.clear_screen'), patch('anr.ui.display_profiles_dashboard'), \
                patch('anr.ui._choice', side_effect=['0']):
            ApplicationUI(self.config, self.pm)._menu_profiles()
        self.config.save.assert_not_called()

    def test_defaults_persistence_export_import_and_duplication_keep_enabled_status(self):
        self.assertTrue(Profile.from_dict({'id': 'old', 'name': 'Old'}).enabled)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'config.json'
            manager = ConfigManager.__new__(ConfigManager)
            manager.config = self.config.config
            with patch('anr.config.CONFIG_FILE', target):
                manager.save()
            restored = Config.from_dict(json.loads(target.read_text('utf-8')))
            self.assertFalse(restored.profiles[1].enabled)
            export = str(Path(directory) / 'disabled.json')
            self.assertTrue(ProfileExporter(self.config).export_profile(self.off, export).success)
            imported = SimpleNamespace(config=Config(), save=Mock())
            with patch('anr.importer.print_info'):
                self.assertTrue(ProfileImporter(imported).import_from_file(export).success)
            self.assertFalse(imported.config.profiles[0].enabled)
        with patch('anr.profile.print_success'):
            copy = self.pm.duplicate_profile('two', 'Disabled copy')
        self.assertFalse(copy.enabled)

    def test_profile_and_schedule_views_keep_disabled_separate_from_active_and_due(self):
        for width in (120, 68, 32):
            screen = Console(file=io.StringIO(), width=width, record=True,
                             theme=Theme(CONSOLE_STYLES), highlight=False)
            with patch.object(views, 'console', screen), patch.object(output, 'console', screen):
                views.display_profiles_dashboard(self.config)
                views.display_main_dashboard(self.config, True)
                text = screen.export_text()
            self.assertIn('Disabled', text)
            self.assertIn('Active', text)
            self.assertIn('[e]', text)
            self.assertIn('Manual only', text)
            self.assertTrue(all(cell_len(line) <= width for line in text.splitlines()))
        screen = Console(file=io.StringIO(), width=120, theme=Theme(CONSOLE_STYLES))
        with patch.object(checker, 'console', screen), patch.object(output, 'console', screen):
            ScheduledChecker(Mock(), Mock(), self.config).display_schedule()
        disabled_row = next(line for line in screen.file.getvalue().splitlines() if 'Disabled' in line)
        self.assertNotIn('Due now', disabled_row)


if __name__ == '__main__':
    unittest.main()
