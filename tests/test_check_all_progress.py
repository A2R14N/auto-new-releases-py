import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rich.console import Console
from rich.theme import Theme

from anr import checker
from anr.check_display import CheckAllDisplay
from anr.checker import CheckProgress, CheckStatus, ProfileCheckResult, check_profiles_with_progress
from anr.cli import CLIHandler
from anr.constants import CONSOLE_STYLES
from anr.models import Artist, Profile


class CheckAllProgressTests(unittest.TestCase):
    def setUp(self):
        self.console = Console(file=io.StringIO(), width=68, height=50, force_terminal=True,
                               legacy_windows=False, theme=Theme(CONSOLE_STYLES), highlight=False)
        self.profiles = [Profile(str(i), name, playlist_uri='spotify:playlist:' + str(i),
                                artists=[Artist('a', 'Wiz Khalifa'), Artist('b', 'Artist')])
                         for i, name in enumerate(('TRAP RAP US', 'Nicolae Guta'))]

    def text(self, display):
        with self.console.capture() as capture:
            self.console.print(display.render())
        import re
        return re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', capture.get())

    def test_scan_counts_only_completed_work_and_hides_bar_during_writes(self):
        display = CheckAllDisplay(self.profiles, console=self.console)
        display.start_profile(0)
        display.update(CheckProgress('artist', artist_current=1, artist_total=2, artist_name='Wiz Khalifa'))
        self.assertIn('0/2 scanned', self.text(display))
        display.update(CheckProgress('artist_done', artist_current=1, artist_total=2, artists_checked=1))
        self.assertIn('1/2 scanned', self.text(display))
        display.update(CheckProgress('adding', tracks_found=7))
        self.assertIn('Waiting for confirmation', self.text(display))
        self.assertNotIn('100%', self.text(display))
        self.assertEqual(0, display.total_added)
        display.update(CheckProgress('added', tracks_added=7))
        display.update(CheckProgress('sorting'))
        self.assertIn('Sorting playlist', self.text(display))
        self.assertNotIn('100%', self.text(display))
        self.assertEqual(7, display.total_added)

    def test_partial_and_skipped_results_do_not_become_up_to_date_or_double_count(self):
        display = CheckAllDisplay(self.profiles, console=self.console)
        display.start_profile(0)
        display.update(CheckProgress('added', tracks_added=7))
        display.complete_profile(ProfileCheckResult('TRAP RAP US', CheckStatus.PARTIAL,
            total_tracks_added=7, total_artists=2, artists_checked=1, artists_pending=1))
        self.assertEqual(7, display.total_added)
        display.start_profile(1)
        display.complete_profile(ProfileCheckResult('Nicolae Guta', CheckStatus.SKIPPED, error_message='No playlist'))
        display.finish()
        text = self.text(display)
        self.assertIn('1 pending', text)
        self.assertIn('Skipped', text)
        self.assertIn('finished with issues', text)
        self.assertNotIn('Up to date', text)
        self.assertEqual(1, display.total_pending)

    def test_runner_forwards_dry_run_once_per_configured_profile(self):
        self.profiles[1].playlist_uri = ''
        backend = Mock()
        backend.check_profile.return_value = ProfileCheckResult('TRAP RAP US', CheckStatus.NO_NEW)
        results = check_profiles_with_progress(backend, self.profiles, dry_run=True, enabled=False)
        backend.check_profile.assert_called_once_with(self.profiles[0], progress_callback=None, silent=True, dry_run=True)
        self.assertEqual([CheckStatus.NO_NEW, CheckStatus.SKIPPED], [r.status for r in results])

    def test_quiet_cli_retains_partial_additions_and_nonzero_exit(self):
        backend = Mock()
        backend.check_profile.side_effect = [
            ProfileCheckResult('TRAP RAP US', CheckStatus.PARTIAL, total_tracks_added=7, error_message='Sort pending'),
            ProfileCheckResult('Nicolae Guta', CheckStatus.SUCCESS, total_tracks_added=2),
        ]
        app = SimpleNamespace(release_checker=backend,
            config_manager=SimpleNamespace(config=SimpleNamespace(profiles=self.profiles)))
        handler = CLIHandler(app, quiet=True)
        handler.log = Mock()
        self.assertEqual(1, handler._check_all())
        handler.log.assert_any_call('Total: 9 tracks added', 'warning')
        self.assertTrue(all(c.kwargs['progress_callback'] is None for c in backend.check_profile.call_args_list))

    def test_cancellation_stops_before_next_profile_and_keeps_cancelled_state(self):
        backend = Mock()
        backend.check_profile.side_effect = KeyboardInterrupt
        display = CheckAllDisplay(self.profiles, console=self.console, enabled=False)
        with patch('anr.check_display.CheckAllDisplay', return_value=display):
            with self.assertRaises(KeyboardInterrupt):
                check_profiles_with_progress(backend, self.profiles)
        self.assertEqual(1, backend.check_profile.call_count)
        self.assertTrue(display.cancelled)
        self.assertFalse(display.finished)
        self.assertIn('Check cancelled', self.text(display))

    def test_redirected_output_uses_plain_progress_and_preserves_dry_run_label(self):
        plain = Console(file=io.StringIO(), width=68, height=50, force_terminal=False,
                        theme=Theme(CONSOLE_STYLES))
        def run(profile, progress_callback, **kwargs):
            progress_callback(CheckProgress('artist', artist_current=1, artist_total=2, artist_name='Wiz Khalifa'))
            return ProfileCheckResult(profile.name, CheckStatus.SUCCESS, total_tracks_added=3)
        with patch.object(checker, 'console', plain):
            check_profiles_with_progress(SimpleNamespace(check_profile=run), self.profiles, dry_run=True)
        text = plain.file.getvalue()
        self.assertIn('Checking Wiz Khalifa', text)
        self.assertIn('Would add 6 tracks', text)
        self.assertNotIn('╭', text)

    def test_redirected_partial_dry_run_never_claims_tracks_were_added(self):
        plain = Console(file=io.StringIO(), width=68, force_terminal=False,
                        theme=Theme(CONSOLE_STYLES))
        backend = Mock()
        backend.check_profile.return_value = ProfileCheckResult(
            'TRAP RAP US', CheckStatus.PARTIAL, total_tracks_added=3,
            artists_pending=1, error_message='Artist lookup failed; 1 pending')
        with patch.object(checker, 'console', plain):
            results = check_profiles_with_progress(backend, self.profiles[:1], dry_run=True)
        text = plain.file.getvalue()
        self.assertEqual(CheckStatus.PARTIAL, results[0].status)
        self.assertIn('Would add 3 tracks', text)
        self.assertIn('Artist lookup failed; 1 pending', text)
        self.assertNotIn('added 3 tracks', text.lower())


if __name__ == '__main__':
    unittest.main()
