"""Regression coverage for failed reads, durable recovery and candidate state."""
import math
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from anr.api import SpotifyAPI, SpotifyAPIError, transient_read_error
from anr.bridge_api import BridgeAPI
from anr.bridge_server import BridgeResponseError, _PendingRequest
from anr.checker import CheckStatus, ReleaseChecker, ScheduledChecker
from anr.models import Artist, Config, Profile
from anr.playlist import PlaylistOperations


def song(key, name=None):
    return {'uri': f'spotify:track:{key}', 'name': name or key,
            'artists': [{'name': 'Artist'}]}


def release(key, day=None):
    return {'uri': f'spotify:album:{key}', 'name': key,
            'release_date': day or date.today().isoformat()}


class API:
    def __init__(self, count):
        self.calls = []
        self.artists = {f'spotify:artist:a{i}': [release(f'a{i}')] for i in range(count)}
        self.albums = {f'spotify:album:a{i}': [song(f'a{i}')] for i in range(count)}
        self.batch_errors, self.single_errors, self.album_errors = {}, {}, {}
        self.details = []

    def get_artist_albums_batch(self, uris, **_):
        self.calls.append(('batch', list(uris)))
        result = {}
        for uri in uris:
            error = self.batch_errors.get(uri)
            result[uri] = {'releases': [] if error else self.artists[uri],
                           'error': str(error) if error else None,
                           'error_details': {'status': getattr(error, 'http_status', None),
                                             'retry_after': getattr(error, 'retry_after', None)}}
        return result

    def get_artist_albums(self, uri, **_):
        self.calls.append(('artist', uri))
        if uri in self.single_errors:
            raise self.single_errors[uri]
        return self.artists[uri]

    def get_album(self, uri):
        self.calls.append(('album', uri))
        if uri in self.album_errors:
            raise self.album_errors[uri]
        return {'tracks': {'items': self.albums[uri]}}

    def get_multiple_tracks(self, uris):
        self.calls.append(('tracks', list(uris)))
        return self.details


class Ops:
    def __init__(self, api):
        self.api, self.rows = api, []
        self.accept_count = None
        self.ambiguous = False

    def get_playlist_tracks(self, _):
        return [SimpleNamespace(uri=uri, name=uri, primary_artist='Artist') for uri in self.rows]

    def add_tracks(self, _, uris):
        self.api.calls.append(('write', list(uris)))
        count = len(uris) if self.accept_count is None else self.accept_count
        self.rows.extend(uris[:count])
        return (0, len(uris)) if self.ambiguous else (count, len(uris) - count)


def fixture(count=2, **settings):
    api = API(count)
    ops = Ops(api)
    profile = Profile(id='p', name='Recovery', playlist_uri='spotify:playlist:p',
                      artists=[Artist(uri, uri.rsplit(':', 1)[-1]) for uri in api.artists],
                      sort_by_date=False, **settings)
    manager = SimpleNamespace(config=Config(profiles=[profile]), save=Mock())
    return ReleaseChecker(api, ops, manager), api, ops, profile, manager


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch('anr.checker.time.sleep').start()
        patch('anr.checker.print_warning').start()
        self.addCleanup(patch.stopall)

    def test_healthy_48_artist_scan_keeps_twelve_batches_without_delay(self):
        checker, api, ops, p, _ = fixture(48)
        result = checker.check_profile(p, silent=True)
        self.assertEqual(CheckStatus.SUCCESS, result.status)
        self.assertEqual(12, sum(kind == 'batch' for kind, _ in api.calls))
        self.assertFalse(any(kind == 'artist' for kind, _ in api.calls))
        self.assertEqual(48, len(ops.rows))
        self.sleep.assert_not_called()

    def test_only_two_failed_artists_retry_after_the_initial_scan(self):
        checker, api, ops, p, _ = fixture(48)
        failed = ['spotify:artist:a38', 'spotify:artist:a41']
        api.batch_errors = {uri: SpotifyAPIError('HttpResponseError') for uri in failed}
        result = checker.check_profile(p, silent=True)
        self.assertEqual(CheckStatus.SUCCESS, result.status)
        self.assertEqual(failed, [uri for kind, uri in api.calls if kind == 'artist'])
        last_batch = max(i for i, (kind, _) in enumerate(api.calls) if kind == 'batch')
        self.assertTrue(all(i > last_batch for i, (kind, _) in enumerate(api.calls) if kind == 'artist'))
        self.assertEqual(48, result.artists_checked)
        self.assertEqual({}, p.pending_artists)
        self.assertEqual(48, len(ops.rows))

    def test_persistent_failures_report_46_of_48_and_survive_restart(self):
        checker, api, ops, p, manager = fixture(48)
        failed = ['spotify:artist:a38', 'spotify:artist:a41']
        api.batch_errors = api.single_errors = {uri: SpotifyAPIError('HTTP 503', http_status=503) for uri in failed}
        first = checker.check_profile(p, silent=True)
        self.assertEqual(CheckStatus.PARTIAL, first.status)
        self.assertEqual((46, 2), (first.artists_checked, first.artists_pending))
        self.assertIn('46/48 checked, 2 pending', first.error_message)
        recovered = Profile.from_dict(p.to_dict())
        self.assertEqual(p.pending_artists, recovered.pending_artists)
        api.batch_errors = api.single_errors = {}
        api.calls.clear()
        checker.check_profile(recovered, silent=True)
        self.assertEqual([failed], [uris for kind, uris in api.calls if kind == 'batch'])
        self.assertEqual(48, len(ops.rows))
        self.assertEqual({}, recovered.pending_artists)

    def test_permanent_error_is_not_retried_in_the_same_run(self):
        checker, api, _, p, _ = fixture()
        api.batch_errors[p.artists[0].uri] = SpotifyAPIError('HTTP 403', http_status=403)
        checker.check_profile(p, silent=True)
        self.assertFalse(any(kind == 'artist' for kind, _ in api.calls))
        self.sleep.assert_not_called()
        self.assertEqual(1, len(p.pending_artists))

    def test_long_retry_after_defers_recovery_and_blocks_early_manual_attempt(self):
        checker, api, _, p, manager = fixture(1)
        api.batch_errors[p.artists[0].uri] = SpotifyAPIError('HTTP 429', http_status=429, retry_after=600)
        checker.check_profile(p, silent=True)
        self.sleep.assert_not_called()
        self.assertFalse(any(kind == 'artist' for kind, _ in api.calls))
        entry = p.pending_artists[p.artists[0].uri]
        self.assertGreaterEqual(entry['not_before'], p.last_scan + 600)
        self.assertEqual([], ScheduledChecker(api, checker.playlist_ops, manager).get_profiles_due())
        api.calls.clear()
        result = checker.check_profile(p, silent=True)
        self.assertEqual([], api.calls)
        self.assertEqual(1, result.artists_pending)
        self.assertEqual(1, len(p.pending_artists))

    def test_scheduler_only_attempts_pending_artists_whose_recovery_is_due(self):
        checker, api, ops, p, manager = fixture()
        api.batch_errors = {a.uri: SpotifyAPIError('403', http_status=403) for a in p.artists}
        checker.check_profile(p, silent=True)
        api.batch_errors = {}
        api.calls.clear()
        p.pending_artists[p.artists[0].uri]['retry_at'] = 0
        scheduler = ScheduledChecker(api, ops, manager)
        results = scheduler.check_due_profiles(silent=True)
        self.assertEqual(1, len(results))
        self.assertEqual([[p.artists[0].uri]], [v for k, v in api.calls if k == 'batch'])
        self.assertEqual([p.artists[1].uri], list(p.pending_artists))

    def test_native_web_api_does_not_receive_a_second_outer_read_retry(self):
        class NativeAPI(API, SpotifyAPI):
            pass
        checker, _, _, p, _ = fixture(1)
        api = NativeAPI(1)
        api.batch_errors[p.artists[0].uri] = SpotifyAPIError('503', http_status=503)
        checker.api = api
        from anr.api import ReleaseFetcher
        checker.release_fetcher = ReleaseFetcher(api)
        checker.check_profile(p, silent=True)
        self.assertFalse(any(k == 'artist' for k, _ in api.calls))
        self.sleep.assert_not_called()

    def test_cooldown_prevents_other_failed_reads_from_retrying_early(self):
        checker, api, _, p, _ = fixture()
        api.batch_errors = {p.artists[0].uri: SpotifyAPIError('HTTP 429', http_status=429, retry_after=600),
                            p.artists[1].uri: SpotifyAPIError('HTTP 503', http_status=503)}
        checker.check_profile(p, silent=True)
        self.assertFalse(any(kind == 'artist' for kind, _ in api.calls))
        self.assertTrue(all(v['not_before'] >= p.last_scan + 600 for v in p.pending_artists.values()))

    def test_saved_cutoff_recovers_a_release_that_would_otherwise_age_out(self):
        checker, api, ops, p, _ = fixture(1, days_to_check=1)
        api.artists[p.artists[0].uri] = [release('a0', '2026-01-01')]
        api.batch_errors[p.artists[0].uri] = SpotifyAPIError('HTTP 403', http_status=403)
        initial = datetime(2026, 1, 2).timestamp()
        with patch('anr.checker.time.time', return_value=initial):
            checker.check_profile(p, silent=True)
        api.batch_errors = {}
        with patch('anr.checker.time.time', return_value=initial + 23 * 3600):
            result = checker.check_profile(p, silent=True)
        self.assertEqual(CheckStatus.SUCCESS, result.status)
        self.assertEqual(['spotify:track:a0'], ops.rows)

    def test_a_new_normal_interval_checks_all_artists_again(self):
        checker, api, _, p, _ = fixture()
        api.batch_errors[p.artists[0].uri] = SpotifyAPIError('403', http_status=403)
        checker.check_profile(p, silent=True)
        api.batch_errors = {}
        api.calls.clear()
        with patch('anr.checker.time.time', return_value=p.last_scan + 24 * 3600 + 1):
            checker.check_profile(p, silent=True)
        self.assertEqual([[a.uri for a in p.artists]], [v for kind, v in api.calls if kind == 'batch'])

    def test_shared_release_is_read_once_and_partial_add_is_recoverable(self):
        checker, api, ops, p, _ = fixture()
        api.artists = {a.uri: [release('shared')] for a in p.artists}
        api.albums = {'spotify:album:shared': [song('one'), song('two')]}
        ops.accept_count = 1
        first = checker.check_profile(p, silent=True)
        self.assertEqual(CheckStatus.PARTIAL, first.status)
        self.assertNotIn('spotify:album:shared', p.tracked_releases)
        self.assertEqual(1, sum(kind == 'album' for kind, _ in api.calls))
        ops.accept_count = None
        checker.check_profile(p, silent=True)
        self.assertEqual(['spotify:track:one', 'spotify:track:two'], ops.rows)
        self.assertIn('spotify:album:shared', p.tracked_releases)

    def test_an_uncertain_write_is_reconciled_before_any_later_write(self):
        checker, api, ops, p, _ = fixture()
        ops.ambiguous = True
        checker.check_profile(p, silent=True)
        self.assertEqual(2, len(ops.rows))
        self.assertEqual(1, sum(kind == 'write' for kind, _ in api.calls))
        ops.ambiguous = False
        checker.check_profile(p, silent=True)
        self.assertEqual(2, len(ops.rows))
        self.assertEqual(1, sum(kind == 'write' for kind, _ in api.calls))
        self.assertFalse(p.pending_artists)

    def test_a_failed_album_does_not_discard_other_artists_candidates(self):
        checker, api, ops, p, _ = fixture()
        api.album_errors['spotify:album:a1'] = SpotifyAPIError('HTTP 503', http_status=503)
        result = checker.check_profile(p, silent=True)
        self.assertEqual(CheckStatus.PARTIAL, result.status)
        self.assertEqual(['spotify:track:a0'], ops.rows)
        self.assertIn('spotify:album:a0', p.tracked_releases)
        self.assertNotIn('spotify:album:a1', p.tracked_releases)
        self.assertEqual(1, sum(kind == 'album' and v.endswith(':a0') for kind, v in api.calls))

    def test_album_cap_does_not_seed_similarity_with_discarded_tracks(self):
        checker, api, ops, p, _ = fixture(1, skip_similar_duplicates=True,
            limit_songs_per_album=True, max_songs_per_album=1)
        api.artists[p.artists[0].uri] = [release('album'), release('single')]
        api.albums = {'spotify:album:album': [song('dropped', 'Song'), song('winner', 'Winner')],
                      'spotify:album:single': [song('alternative', 'Song')]}
        api.details = [{**song('dropped'), 'popularity': 1}, {**song('winner'), 'popularity': 100}]
        result = checker.check_profile(p, silent=True)
        self.assertEqual(CheckStatus.SUCCESS, result.status)
        self.assertEqual(['spotify:track:winner', 'spotify:track:alternative'], ops.rows)

    def test_empty_partial_and_unranked_details_remain_pending_without_commits(self):
        for details in ([], [{**song('one'), 'popularity': 1}],
                        [song('one'), song('two')]):
            with self.subTest(details=details):
                checker, api, ops, p, _ = fixture(1, limit_songs_per_album=True, max_songs_per_album=1)
                api.albums['spotify:album:a0'] = [song('one'), song('two')]
                api.details = details
                result = checker.check_profile(p, silent=True)
                self.assertEqual(CheckStatus.ERROR, result.status)
                self.assertEqual([], ops.rows)
                self.assertEqual({}, p.tracked_releases)
                self.assertEqual({}, p.tracked_tracks)
                self.assertTrue(p.pending_artists)

    def test_dry_run_preserves_recovery_state_and_timestamps(self):
        checker, api, _, p, manager = fixture()
        api.batch_errors[p.artists[0].uri] = SpotifyAPIError('403', http_status=403)
        checker.check_profile(p, silent=True)
        before = p.to_dict()
        api.batch_errors = {}
        manager.save.reset_mock()
        checker.check_profile(p, silent=True, dry_run=True)
        self.assertEqual(before, p.to_dict())
        manager.save.assert_not_called()

    def test_removed_artist_does_not_leave_an_unresolvable_pending_entry(self):
        checker, api, _, p, _ = fixture()
        api.batch_errors[p.artists[0].uri] = SpotifyAPIError('403', http_status=403)
        checker.check_profile(p, silent=True)
        p.artists.pop(0)
        result = checker.check_profile(p, silent=True)
        self.assertEqual(CheckStatus.NO_NEW, result.status)
        self.assertEqual({}, p.pending_artists)

    def test_changing_playlist_starts_a_full_scan_instead_of_using_old_recovery(self):
        checker, api, _, p, _ = fixture()
        api.batch_errors[p.artists[0].uri] = SpotifyAPIError('403', http_status=403)
        checker.check_profile(p, silent=True)
        p.playlist_uri = 'spotify:playlist:another'
        api.batch_errors = {}
        api.calls.clear()
        checker.check_profile(p, silent=True)
        self.assertEqual(2, len(next(v for kind, v in api.calls if kind == 'batch')))

    def test_error_classification_and_metadata_survive_rpc_boundary(self):
        pending = _PendingRequest('get_album', {})
        pending.resolve(None, 'HttpResponseError', {'status': 429, 'retry_after': 12})
        server = Mock()
        try:
            pending.wait()
        except BridgeResponseError as error:
            server.call.side_effect = error
        api = BridgeAPI(server)
        with self.assertRaises(SpotifyAPIError) as caught:
            api.get_album('spotify:album:a')
        self.assertEqual((429, 12), (caught.exception.http_status, caught.exception.retry_after))
        self.assertTrue(transient_read_error(caught.exception))
        for code in (401, 403, 404):
            self.assertFalse(transient_read_error(SpotifyAPIError('HttpResponseError', http_status=code)))
        for delay in (math.inf, math.nan, -1, 'invalid'):
            self.assertIsNone(SpotifyAPIError('x', retry_after=delay).retry_after)

    def test_bridge_timeout_does_not_immediately_queue_another_rpc(self):
        server = Mock()
        server.call.side_effect = TimeoutError('Bridge request timed out')
        api = BridgeAPI(server)
        with self.assertRaises(SpotifyAPIError) as caught:
            api.get_artist_albums('spotify:artist:a')
        self.assertFalse(transient_read_error(caught.exception))
        self.assertEqual(1, server.call.call_count)

    def test_bridge_reconciliation_requests_fresh_playlist_contents(self):
        api = BridgeAPI(Mock())
        api._call = Mock(return_value=[])
        PlaylistOperations(api).get_playlist_tracks('spotify:playlist:p', refresh=True)
        api._call.assert_called_once_with('get_playlist_tracks', {'playlist_id': 'p', 'fresh': True})
        checker, _, _, p, manager = fixture(1)
        p.last_scan = datetime.now().timestamp()
        p.pending_artists = {p.artists[0].uri: {'playlist_uri': p.playlist_uri, 'cutoff': None,
            'error': 'Playlist addition not confirmed; pending recovery'}}
        api.get_artist_albums_batch = lambda uris, **_: {uri: {'releases': [], 'error': None} for uri in uris}
        ops = SimpleNamespace(get_playlist_tracks=Mock(return_value=[]))
        result = ReleaseChecker(api, ops, manager).check_profile(p, silent=True)
        self.assertEqual(CheckStatus.NO_NEW, result.status)
        ops.get_playlist_tracks.assert_called_once_with(p.playlist_uri, refresh=True)


if __name__ == '__main__':
    unittest.main()
