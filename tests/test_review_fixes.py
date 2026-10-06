"""Regression coverage for playlist safety, recovery and profile persistence."""
import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from anr.api import SpotifyAPIError
from anr.bridge_api import BridgeAPI
from anr.checker import CheckStatus, ReleaseChecker, ScheduledChecker
from anr.cli import CLIHandler
from anr.importer import ImportMode, ProfileExporter, ProfileImporter
from anr.models import Artist, Config, Profile
from anr.playlist import PlaylistBackup, PlaylistOperations, PlaylistRestorer
from anr.tools import PlaylistDeduplicator, PlaylistSorter, ArtistTrackRemover, SortCriteria
from test_web_api import fixture, checker_fixture


class PlaylistSafetyTests(unittest.TestCase):
    def test_dedupe_keeps_one_copy_and_sends_position_specific_delete(self):
        http, api, ops = fixture()
        http.rows = ['existing', 'existing', 'new']
        result = PlaylistDeduplicator(api, ops).remove_duplicates('spotify:playlist:p', create_backup=False)
        self.assertTrue(result.success, result.error_message)
        self.assertEqual(1, result.duplicates_removed)
        self.assertEqual(['existing', 'new'], http.rows)
        body = next(body for method, path, params, body in http.calls if method == 'DELETE')
        self.assertEqual([{'uri':'spotify:track:existing', 'positions':[1]}], body['items'])
        self.assertEqual('snapshot', body['snapshot_id'])

    def test_dedupe_batches_descending_positions_without_removing_originals(self):
        http, api, ops = fixture()
        http.rows = ['existing'] * 205 + ['new'] * 104
        result = PlaylistDeduplicator(api, ops).remove_duplicates('spotify:playlist:p', create_backup=False)
        self.assertTrue(result.success, result.error_message)
        self.assertEqual(['existing', 'new'], http.rows)
        self.assertEqual(307, result.duplicates_removed)
        deletes = [body for method, path, params, body in http.calls if method == 'DELETE']
        self.assertEqual(4, len(deletes))
        self.assertGreater(deletes[0]['items'][-1]['positions'][0], deletes[1]['items'][0]['positions'][0])

    def test_dedupe_rejects_changed_scan_without_writes(self):
        http, api, ops = fixture()
        http.rows = ['existing', 'existing', 'new']
        tracks = ops.get_playlist_tracks('spotify:playlist:p')
        http.rows.insert(0, 'new')
        removed, failed = ops.remove_track_occurrences('spotify:playlist:p', tracks, [1])
        self.assertEqual((0, 1), (removed, failed))
        self.assertFalse(any(method == 'DELETE' for method, *_ in http.calls))

    def test_dedupe_write_failure_is_not_success(self):
        http, api, ops = fixture()
        http.rows = ['existing', 'existing']
        with patch.object(api, 'remove_playlist_occurrences', side_effect=SpotifyAPIError('Write failed')):
            result = PlaylistDeduplicator(api, ops).remove_duplicates('spotify:playlist:p', create_backup=False)
        self.assertFalse(result.success)
        self.assertEqual(['existing', 'existing'], http.rows)

    def test_similar_dedupe_preserves_first_song_and_bridge_dispatches_exact_uids(self):
        http, api, ops = fixture()
        http.rows = ['existing','new']
        original_track = http.track
        def track(name):
            data = original_track(name)
            data['name'] = 'Same title'
            return data
        http.track = track
        result = PlaylistDeduplicator(api, ops).remove_duplicates('spotify:playlist:p', include_similar=True, create_backup=False)
        self.assertTrue(result.success, result.error_message)
        self.assertEqual(['existing'],http.rows)
        api = BridgeAPI(Mock())
        api._call = Mock(return_value={'success':True})
        tracks = [SimpleNamespace(uri='spotify:track:a',uid='first'),SimpleNamespace(uri='spotify:track:a',uid='second')]
        self.assertEqual((1,0),PlaylistOperations(api).remove_track_occurrences('spotify:playlist:p',tracks,[1]))
        args = api._call.call_args.args
        self.assertEqual('remove_playlist_rows',args[0])
        self.assertEqual(['second'],args[1]['row_uids'])
        self.assertEqual(['first','second'],args[1]['expected_uids'])

    def test_incomplete_changed_or_unreadable_playlist_reads_abort_sorts(self):
        for mode in ('empty_page', 'changed_total', 'null_item', 'early_end', 'missing_total'):
            with self.subTest(mode=mode):
                http, api, ops = fixture()
                http.rows = ['existing', 'new']
                original = ops.client.playlist_items
                def page(*args, **kwargs):
                    data = original(*args, **kwargs)
                    if mode == 'early_end':
                        data['next'] = None
                    elif mode == 'missing_total':
                        data.pop('total')
                    elif kwargs.get('offset', 0):
                        if mode == 'empty_page': data['items'] = []
                        if mode == 'changed_total': data['total'] = 3
                        if mode == 'null_item': data['items'] = [{'item':None}]
                    return data
                with patch.object(ops.client, 'playlist_items', side_effect=page):
                    result = PlaylistSorter(api, ops).sort_playlist('spotify:playlist:p', SortCriteria.TRACK_NAME, create_backup=False)
                self.assertFalse(result.success)
                self.assertEqual(['existing', 'new'], http.rows)
                self.assertFalse(any(method in ('PUT','POST','DELETE') for method, *_ in http.calls))

    def test_failed_bridge_read_stops_checker_without_adding_or_tracking(self):
        server = Mock()
        server.call.side_effect = RuntimeError('Playlist disconnected')
        api = BridgeAPI(server)
        manager = SimpleNamespace(config=Config(), save=Mock())
        profile = Profile(id='p', name='P', playlist_uri='spotify:playlist:p', artists=[Artist('spotify:artist:a','A')])
        with contextlib.redirect_stderr(io.StringIO()):
            result = ReleaseChecker(api, PlaylistOperations(api), manager).check_profile(profile, silent=True)
        self.assertEqual(CheckStatus.ERROR, result.status)
        self.assertEqual({}, profile.tracked_releases)
        manager.save.assert_not_called()
        self.assertEqual(['get_playlist_tracks'], [call.args[0] for call in server.call.call_args_list])

    def test_all_destructive_tools_abort_if_backup_cannot_be_saved(self):
        http, api, ops = fixture()
        http.rows = ['existing', 'existing']
        with patch.object(PlaylistBackup, 'create', return_value=False), patch('rich.prompt.Confirm.ask', return_value=True):
            sort = PlaylistSorter(api, ops).sort_playlist('spotify:playlist:p', SortCriteria.TRACK_NAME)
            dedupe = PlaylistDeduplicator(api, ops).remove_duplicates('spotify:playlist:p')
            interactive = PlaylistDeduplicator(api, ops).interactive_dedupe('spotify:playlist:p')
            removed, _ = ArtistTrackRemover(api, ops).remove_artist_tracks('spotify:playlist:p', 'spotify:artist:a')
        self.assertFalse(sort.success)
        self.assertFalse(dedupe.success)
        self.assertFalse(interactive.success)
        self.assertEqual(0, removed)
        self.assertFalse(any(method in ('PUT','POST','DELETE') for method, *_ in http.calls))

    def test_local_files_cannot_be_destructively_rewritten_by_web_api(self):
        http, api, ops = fixture()
        self.assertFalse(ops.replace_all_tracks('spotify:playlist:p',['spotify:local:a:b:c:100']))
        self.assertEqual(['existing'],http.rows)
        self.assertFalse(any(method in ('PUT','POST','DELETE') for method, *_ in http.calls))


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'backups.json'
        patcher = patch.object(PlaylistBackup, 'BACKUP_FILE', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_failed_first_playlist_backup_survives_second_playlist_success(self):
        self.assertTrue(PlaylistBackup.create('first', 'First', ['a'], 'sort_by_release_date'))
        self.assertTrue(PlaylistBackup.create('second', 'Second', ['b'], 'sort_by_release_date'))
        PlaylistBackup.complete('second')
        self.assertEqual('first', PlaylistBackup.get_pending()['playlist_uri'])
        self.assertIsNone(PlaylistBackup.get_pending('second'))

    def test_original_backup_survives_retries_and_rejects_missing_songs(self):
        PlaylistBackup.create('first', 'First', ['a','a','b'], 'sort_by_release_date')
        self.assertTrue(PlaylistBackup.create('first', 'First', ['b','a','a'], 'sort_by_release_date'))
        self.assertFalse(PlaylistBackup.create('first', 'First', ['a','b'], 'sort_by_release_date'))
        self.assertEqual(['a','a','b'], PlaylistBackup.get_pending()['track_uris'])

    def test_legacy_and_old_backups_are_retained_and_migrated(self):
        legacy = {'playlist_uri':'first', 'track_uris':['a'], 'status':'in_progress',
                  'operation':'sort_by_release_date', 'created_at':time.time()-72*3600}
        self.path.write_text(json.dumps(legacy), encoding='utf-8')
        self.assertEqual(legacy, PlaylistBackup.get_pending())
        PlaylistBackup.create('second','Second',['b'],'deduplicate')
        self.assertEqual(2, len(PlaylistBackup.get_pending_all()))
        PlaylistBackup.discard('second')
        self.assertEqual(legacy, PlaylistBackup.get_pending())

    def test_corrupted_backup_cannot_be_overwritten(self):
        self.path.write_text('{broken', encoding='utf-8')
        self.assertFalse(PlaylistBackup.create('first','First',['a'],'sort_by_release_date'))
        self.assertEqual('{broken', self.path.read_text('utf-8'))

    def test_restore_completes_only_selected_playlist(self):
        PlaylistBackup.create('first','First',['a'],'sort_by_release_date')
        PlaylistBackup.create('second','Second',['b'],'sort_by_release_date')
        ops = SimpleNamespace(replace_all_tracks=Mock(return_value=True))
        self.assertTrue(PlaylistRestorer(ops).restore(PlaylistBackup.get_pending('second')))
        self.assertEqual('first', PlaylistBackup.get_pending()['playlist_uri'])

    def test_sort_without_backup_does_not_delete_unrelated_recovery(self):
        PlaylistBackup.create('first','First',['a'],'sort_by_release_date')
        http, api, ops = fixture()
        result = PlaylistSorter(api,ops).sort_playlist('spotify:playlist:p',SortCriteria.TRACK_NAME,create_backup=False)
        self.assertTrue(result.success,result.error_message)
        self.assertEqual('first',PlaylistBackup.get_pending()['playlist_uri'])


class ProfileStateTests(unittest.TestCase):
    def test_bridge_missing_popularity_is_an_error_without_history_mutations(self):
        http, _api, _ops, manager, profile, _checker = checker_fixture()
        api = BridgeAPI(Mock())
        api.get_artist_albums_batch = lambda uris, **kwargs: {uris[0]: {'releases':[{'uri':'spotify:album:a','release_date':'2026-10-06'}], 'error':None}}
        api.get_album = lambda uri: {'tracks':{'items':[{'uri':'spotify:track:a'}]}}
        ops = SimpleNamespace(get_playlist_tracks=lambda uri: [], add_tracks=Mock())
        profile.skip_low_popularity = True
        profile.days_to_check = 0
        with contextlib.redirect_stderr(io.StringIO()):
            result = ReleaseChecker(api, ops, manager).check_profile(profile, silent=True)
        self.assertEqual(CheckStatus.ERROR, result.status)
        self.assertEqual({}, profile.tracked_releases)
        self.assertEqual({}, profile.tracked_tracks)
        manager.save.assert_not_called()
        ops.add_tracks.assert_not_called()

    def test_failed_sort_retries_without_new_tracks_and_is_due_until_saved(self):
        http, api, ops, manager, profile, checker = checker_fixture()
        calls = []
        outcomes = iter([False, True])
        def sort(uri):
            calls.append((uri, profile.pending_sort, manager.save.call_count))
            return SimpleNamespace(success=next(outcomes), error_message='Write failed')
        with patch('anr.tools.PlaylistTools', return_value=SimpleNamespace(sorter=SimpleNamespace(sort_by_release_date=sort))):
            first = checker.check_profile(profile, silent=True)
            self.assertEqual(CheckStatus.PARTIAL, first.status)
            self.assertTrue(profile.pending_sort)
            self.assertIsNone(profile.last_check)
            manager.config.profiles = [profile]
            profile.last_check = time.time()
            self.assertEqual([profile], ScheduledChecker(api,ops,manager).get_profiles_due())
            before = list(http.rows)
            second = checker.check_profile(profile, silent=True)
        self.assertEqual(CheckStatus.NO_NEW, second.status)
        self.assertFalse(profile.pending_sort)
        self.assertEqual(before, http.rows)
        self.assertEqual(2, len(calls))
        self.assertTrue(all(pending for uri, pending, saves in calls))
        self.assertGreaterEqual(calls[0][2], 1)

    def test_pending_sort_dry_run_does_not_write_and_failed_retry_does_not_add(self):
        http, api, ops, manager, profile, checker = checker_fixture()
        profile.pending_sort = True
        sorter = Mock(return_value=SimpleNamespace(success=False,error_message='Restore backup first'))
        with patch('anr.tools.PlaylistTools', return_value=SimpleNamespace(sorter=SimpleNamespace(sort_by_release_date=sorter))):
            checker.check_profile(profile, silent=True, dry_run=True)
            sorter.assert_not_called()
            manager.save.assert_not_called()
            result = checker.check_profile(profile, silent=True)
        self.assertEqual(CheckStatus.PARTIAL, result.status)
        self.assertTrue(profile.pending_sort)
        self.assertEqual(['existing'], http.rows)

    def test_legacy_release_date_backup_is_retried_before_new_additions(self):
        http, api, ops, manager, profile, checker = checker_fixture()
        sorter = Mock(return_value=SimpleNamespace(success=False,error_message='Recover backup first'))
        backup = {'playlist_uri':profile.playlist_uri,'operation':'sort_by_release_date'}
        with patch.object(PlaylistBackup,'get_pending',return_value=backup), patch('anr.tools.PlaylistTools',return_value=SimpleNamespace(sorter=SimpleNamespace(sort_by_release_date=sorter))):
            result = checker.check_profile(profile,silent=True)
        self.assertEqual(CheckStatus.PARTIAL,result.status)
        self.assertTrue(profile.pending_sort)
        self.assertEqual(['existing'],http.rows)
        sorter.assert_called_once()
        manager.save.assert_called_once()

    def test_named_cli_check_does_not_switch_saved_active_profile(self):
        http, api, ops, manager, target, checker = checker_fixture()
        original = Profile(id='original', name='Original')
        manager.config = Config(profiles=[original,target], active_profile_id=original.id)
        saved = []
        manager.save.side_effect = lambda: saved.append(manager.config.to_dict())
        app = SimpleNamespace(config_manager=manager, release_checker=checker)
        handler = CLIHandler(app, quiet=True)
        with patch('anr.tools.PlaylistTools', return_value=SimpleNamespace(sorter=SimpleNamespace(sort_by_release_date=Mock(return_value=SimpleNamespace(success=True))))):
            self.assertEqual(0, handler._check_profile(target.name))
        self.assertEqual('original', manager.config.active_profile_id)
        self.assertTrue(saved)
        self.assertTrue(all(data['active_profile_id']=='original' for data in saved))

    def test_import_round_trip_preserves_history_case_settings_and_explicit_clears(self):
        profile = Profile(id='source', name='Source', tracked_releases={'spotify:album:AbCdE123':1},
                          tracked_tracks={'spotify:track:XyZ456':2}, skip_similar_duplicates=True, pending_sort=True)
        manager = SimpleNamespace(config=Config(), save=Mock())
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/'profile.json')
            self.assertTrue(ProfileExporter(manager).export_profile(profile,path).success)
            result = ProfileImporter(manager).import_from_file(path)
            self.assertTrue(result.success, result.error_message)
            restored = manager.config.profiles[0]
            self.assertEqual(profile.tracked_releases, restored.tracked_releases)
            self.assertEqual(profile.tracked_tracks, restored.tracked_tracks)
            self.assertTrue(restored.skip_similar_duplicates)
            self.assertTrue(restored.pending_sort)
            profile.tracked_releases = {}
            profile.tracked_tracks = {}
            profile.skip_similar_duplicates = False
            profile.pending_sort = False
            ProfileExporter(manager).export_profile(profile,path)
            self.assertTrue(ProfileImporter(manager).import_from_file(path, ImportMode.REPLACE).success)
            self.assertEqual({}, restored.tracked_releases)
            self.assertEqual({}, restored.tracked_tracks)
            self.assertFalse(restored.skip_similar_duplicates)
            self.assertFalse(restored.pending_sort)

    def test_camel_case_import_preserves_uri_keys_and_no_history_removes_both_maps(self):
        manager = SimpleNamespace(config=Config(),save=Mock())
        importer = ProfileImporter(manager)
        data = importer._normalize_keys({'trackedReleases':{'spotify:album:AbCdE123':1},
                                         'trackedTracks':{'spotify:track:XyZ456':2},'skipSimilarDuplicates':True})
        profile = importer._create_profile_from_data(data,'Source')
        self.assertEqual({'spotify:album:AbCdE123':1},profile.tracked_releases)
        self.assertEqual({'spotify:track:XyZ456':2},profile.tracked_tracks)
        self.assertTrue(profile.skip_similar_duplicates)
        manager.config.profiles = [profile]
        with tempfile.TemporaryDirectory() as directory:
            for all_profiles in (False,True):
                path = str(Path(directory)/'profile.json')
                exporter = ProfileExporter(manager)
                result = exporter.export_all_profiles(path,False) if all_profiles else exporter.export_profile(profile,path,False)
                self.assertTrue(result.success)
                data = json.loads(Path(path).read_text('utf-8'))
                exported = data['profiles'][0] if all_profiles else data['profile']
                self.assertEqual({},exported['tracked_releases'])
                self.assertEqual({},exported['tracked_tracks'])
