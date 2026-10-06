"""Exercise the real Spotipy transport without credentials, bridge or network."""
import contextlib
import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import requests
import spotipy

from anr import Application
from anr.api import SpotifyAPI, SpotifyAPIError
from anr.auth import SpotifyAuthManager
from anr.checker import CheckStatus, ReleaseChecker
from anr.models import Artist, Config, Profile
from anr.playlist import PlaylistOperations, PlaylistTrack
from anr.tools import PlaylistSorter, SortCriteria


class FakeHTTP:
    """Simulated current Development Mode responses at the HTTP boundary."""
    def __init__(self):
        self.calls = []
        self.rows = ['existing']
        self.bulk_status = 403
        self.fail_add = False
        self.bad_album_page = False

    def track(self, name):
        return {'uri': f'spotify:track:{name}', 'id': name,
                'name': 'New (Remix)' if name == 'remix' else name,
                'artists': [{'uri': 'spotify:artist:a', 'name': 'Artist'}],
                'album': {'uri': 'spotify:album:old' if name == 'existing' else 'spotify:album:release',
                          'name': 'Album', 'release_date': '2025-01-01' if name == 'existing' else date.today().isoformat()},
                'duration_ms': 1234}

    def request(self, method, url, **kwargs):
        parsed = urlparse(url)
        path = parsed.path.rstrip('/').removeprefix('/v1/')
        params = {**{key:values[0] for key,values in parse_qs(parsed.query).items()}, **(kwargs.get('params') or {})}
        body = json.loads(kwargs['data']) if kwargs.get('data') else None
        self.calls.append((method, path, params, body))
        status = 200
        if path == 'me':
            data = {'id':'user', 'display_name':'Test'}
        elif path == 'me/playlists':
            playlist = {'id':'p', 'uri':'spotify:playlist:p', 'name':'Playlist', 'owner':{'id':'user'}, 'items':{'total':len(self.rows)}}
            data = playlist if method == 'POST' else {'items':[playlist], 'next':None}
        elif path == 'playlists/p':
            if params.get('fields'):
                raise AssertionError('Field selection must support both playlist response shapes')
            data = {'id':'p','uri':'spotify:playlist:p','name':'Playlist','owner':{'id':'user'},'items':{'total':len(self.rows)}}
        elif path == 'playlists/p/items':
            if method == 'GET':
                if params.get('fields'):
                    raise AssertionError('Legacy track field selection would reject current item responses')
                offset = int(params.get('offset',0))
                ids = self.rows[offset:offset+1]
                data = {'items':[{'added_at':'2026-10-01','item':self.track(name)} for name in ids],
                        'total':len(self.rows), 'next':'next-page' if offset+len(ids)<len(self.rows) else None}
            elif method == 'PUT':
                self.rows = [uri.split(':')[-1] for uri in body['uris']]
                data = {'snapshot_id':'snapshot'}
            elif method == 'POST':
                if self.fail_add:
                    status, data = 500, {'error':{'message':'add failed'}}
                else:
                    position = body.get('position', len(self.rows))
                    self.rows[position:position] = [uri.split(':')[-1] for uri in body['uris']]
                    data = {'snapshot_id':'snapshot'}
            elif method == 'DELETE':
                self.assert_modern_delete(body)
                ids = {item['uri'].split(':')[-1] for item in body['items']}
                self.rows = [name for name in self.rows if name not in ids]
                data = {'snapshot_id':'snapshot'}
        elif path == 'artists/a/albums':
            data = {'items':[{'uri':'spotify:album:release','id':'release','name':'New release','release_date':date.today().isoformat()}], 'next':None}
        elif path == 'albums/release':
            data = {'uri':'spotify:album:release','name':'New release','tracks':{
                'items':[self.track('existing'),self.track('new')], 'total':3,
                'next':'https://api.spotify.com/v1/albums/release/tracks?offset=2'}}
        elif path == 'albums/release/tracks':
            data = {'items':[] if self.bad_album_page else [self.track('remix')], 'total':3, 'next':None}
        elif path in ('tracks','artists'):
            status = self.bulk_status
            if status == 200:
                ids = params['ids'].split(',')
                data = {path:[self.track(name) if path=='tracks' else {'id':name,'uri':f'spotify:artist:{name}'} for name in ids]}
            else:
                data = {'error':{'message':'bulk endpoint unavailable'}}
        elif path.startswith('tracks/'):
            data = self.track(path.split('/')[-1])
        elif path.startswith('artists/'):
            artist_id = path.split('/')[-1]
            data = {'uri':f'spotify:artist:{artist_id}','id':artist_id,'name':'Artist','images':[]}
        else:
            raise AssertionError(f'Unexpected Web API request: {method} {path}')
        response = requests.Response()
        response.status_code = status
        response.url = url
        response.headers['Retry-After'] = '7'
        response._content = json.dumps(data).encode()
        return response

    @staticmethod
    def assert_modern_delete(body):
        assert 'items' in body and 'tracks' not in body


def fixture():
    http = FakeHTTP()
    session = requests.Session()
    session.request = http.request
    client = spotipy.Spotify(auth='test-token-unused-outside-fixture', requests_session=session, retries=0)
    api = SpotifyAPI(SimpleNamespace(get_client=lambda:client))
    api._rate_limit_delay = 0
    return http, api, PlaylistOperations(api)


def checker_fixture():
    http, api, ops = fixture()
    manager = SimpleNamespace(config=Config(), save=Mock())
    profile = Profile(id='test', name='Test', artists=[Artist(uri='spotify:artist:a',name='Artist')],
                      playlist_uri='spotify:playlist:p', sort_by_date=True, skip_remixes=True)
    return http, api, ops, manager, profile, ReleaseChecker(api,ops,manager)


class WebAPITests(unittest.TestCase):
    def test_new_and_legacy_playlist_item_shapes_are_supported(self):
        http, _api, ops = fixture()
        http.rows = ['existing','new']
        rows = ops.get_playlist_tracks('spotify:playlist:p')
        self.assertEqual(http.rows, [row.uri.split(':')[-1] for row in rows])
        self.assertEqual(2, len([call for call in http.calls if call[1]=='playlists/p/items']))
        self.assertEqual('spotify:track:new', PlaylistTrack.from_playlist_item({'track':http.track('new')}).uri)

    def test_metadata_and_playlist_creation_use_current_contract(self):
        http, api, _ops = fixture()
        self.assertEqual(1, api.get_playlist('spotify:playlist:p')['tracks']['total'])
        self.assertEqual(1, api.get_user_playlists()[0]['tracks']['total'])
        self.assertEqual('p', api.create_playlist('Created')['id'])
        self.assertTrue(any(method=='POST' and path=='me/playlists' for method,path,*_ in http.calls))

    def test_full_album_pagination_reuses_first_page_and_caches_only_complete_reads(self):
        http, api, _ops = fixture()
        album = api.get_album('spotify:album:release')
        self.assertEqual(3, len(album['tracks']['items']))
        self.assertEqual(album, api.get_album('spotify:album:release'))
        self.assertEqual(1, len([c for c in http.calls if c[1]=='albums/release']))
        self.assertEqual(1, len([c for c in http.calls if c[1]=='albums/release/tracks']))
        http, api, _ops = fixture()
        http.bad_album_page = True
        with self.assertRaises(SpotifyAPIError):
            api.get_album('spotify:album:release')
        self.assertNotIn('album:release', api._cache)

    def test_bulk_fallback_preserves_order_and_stops_retrying_removed_endpoints(self):
        http, api, _ops = fixture()
        for _ in range(2):
            self.assertEqual(['spotify:track:new','spotify:track:existing'],
                             [t['uri'] for t in api.get_multiple_tracks(['spotify:track:new','spotify:track:existing'])])
            self.assertEqual(['a','b'], [a['id'] for a in api.get_multiple_artists(['a','b'])])
        self.assertEqual(1, len([c for c in http.calls if c[1]=='tracks']))
        self.assertEqual(1, len([c for c in http.calls if c[1]=='artists']))

    def test_authentication_and_rate_limit_errors_do_not_trigger_bulk_fallback(self):
        for status, message in [(401,'Authentication expired'),(429,'7 seconds')]:
            http, api, _ops = fixture()
            http.bulk_status = status
            with self.assertRaisesRegex(SpotifyAPIError, message):
                api.get_multiple_tracks(['spotify:track:new'])
            self.assertFalse(any(c[1]=='tracks/new' for c in http.calls))

    def test_apps_with_bulk_access_keep_using_bulk_requests(self):
        http, api, _ops = fixture()
        http.bulk_status=200
        self.assertEqual(2,len(api.get_multiple_tracks(['spotify:track:new','spotify:track:existing'])))
        self.assertEqual(2,len(api.get_multiple_artists(['a','b'])))
        self.assertEqual(['tracks','artists'],[c[1] for c in http.calls])

    def test_dry_run_uses_direct_api_without_tracking_or_playlist_writes(self):
        http, _api, _ops, manager, profile, checker = checker_fixture()
        result = checker.check_profile(profile,silent=True,dry_run=True)
        self.assertEqual(CheckStatus.SUCCESS,result.status)
        self.assertEqual(1,result.total_tracks_added)
        self.assertTrue(all(c[0]=='GET' for c in http.calls))
        self.assertEqual({},profile.tracked_tracks)
        self.assertEqual({},profile.tracked_releases)
        manager.save.assert_not_called()

    def test_full_check_adds_only_new_non_remix_tracks_and_sorts_by_release_date(self):
        http, _api, _ops, manager, profile, checker = checker_fixture()
        with patch('anr.tools.PlaylistBackup.create'), patch('anr.tools.PlaylistBackup.complete'):
            result = checker.check_profile(profile,silent=True)
        self.assertEqual(CheckStatus.SUCCESS,result.status,result.error_message)
        self.assertEqual(['new','existing'],http.rows)
        self.assertIn('spotify:album:release',profile.tracked_releases)
        self.assertIn('spotify:track:new',profile.tracked_tracks)
        manager.save.assert_called_once()

    def test_failed_sort_tail_batch_is_reported_and_backup_is_retained(self):
        http, api, ops = fixture()
        http.fail_add = True
        self.assertFalse(ops.replace_all_tracks('spotify:playlist:p',[f'spotify:track:{i}' for i in range(101)]))
        self.assertEqual(100,len(http.rows))
        self.assertNotIn('playlist:p',api._cache)

    def test_removal_and_empty_replacement_use_current_endpoints(self):
        http, _api, ops = fixture()
        self.assertEqual((1,0),ops.remove_tracks('spotify:playlist:p',['spotify:track:existing']))
        self.assertEqual([],http.rows)
        http.rows=['existing']
        self.assertTrue(ops.replace_all_tracks('spotify:playlist:p',[]))
        self.assertEqual([],http.rows)

    def test_positioned_add_batches_preserve_order_and_send_documented_json_objects(self):
        http, _api, ops = fixture()
        uris = [f'spotify:track:{i}' for i in range(101)]
        self.assertEqual((101,0),ops.add_tracks('spotify:playlist:p',uris,position=0))
        self.assertEqual([str(i) for i in range(101)]+['existing'],http.rows)
        posts = [c for c in http.calls if c[0]=='POST']
        self.assertEqual([0,100],[c[3]['position'] for c in posts])
        self.assertEqual([100,1],[len(c[3]['uris']) for c in posts])

    def test_incomplete_sort_keeps_recovery_backup_pending(self):
        http, api, ops = fixture()
        http.rows=[str(i) for i in range(101)]
        http.fail_add=True
        with patch('anr.tools.PlaylistBackup.create') as create, patch('anr.tools.PlaylistBackup.complete') as complete:
            result=PlaylistSorter(api,ops).sort_playlist('spotify:playlist:p',criteria=SortCriteria.TRACK_NAME)
        self.assertFalse(result.success)
        self.assertIn('backup retained',result.error_message)
        create.assert_called_once()
        complete.assert_not_called()

    def test_missing_popularity_does_not_silently_filter_or_sort_using_zeros(self):
        http, api, ops, manager, profile, checker = checker_fixture()
        profile.skip_low_popularity=True
        with patch('anr.checker.traceback.print_exc'):
            result=checker.check_profile(profile,silent=True)
        self.assertEqual(CheckStatus.ERROR,result.status)
        self.assertIn('popularity',result.error_message)
        manager.save.assert_not_called()
        http.rows=['existing','new']
        result=PlaylistSorter(api,ops).sort_playlist('spotify:playlist:p',criteria=SortCriteria.POPULARITY,create_backup=False)
        self.assertFalse(result.success)
        self.assertIn('popularity',result.error_message)
        self.assertTrue(all(c[0]=='GET' for c in http.calls))

    def test_popularity_based_album_limit_does_not_process_unranked_tracks(self):
        http, _api, _ops, manager, profile, checker=checker_fixture()
        profile.skip_remixes=False
        profile.limit_songs_per_album=True
        profile.max_songs_per_album=1
        with patch('anr.checker.traceback.print_exc'):
            result=checker.check_profile(profile,silent=True)
        self.assertEqual(CheckStatus.ERROR,result.status)
        self.assertIn('per-album popularity limit',result.error_message)
        self.assertTrue(all(c[0]=='GET' for c in http.calls))
        manager.save.assert_not_called()

    def test_initialization_selects_oauth_when_no_bridge_is_connected(self):
        _http, api, _ops = fixture()
        manager=SimpleNamespace(config=Config(spotify_client_id='test-id',spotify_client_secret='test-secret'))
        auth=Mock()
        auth.ensure_authenticated.return_value=True
        auth.get_client.return_value=api.client
        bridge=Mock(extension_connected=False)
        services=['ProfileManager','PlaylistOperations','PlaylistSelector','PlaylistRestorer','ReleaseChecker',
                  'InteractiveChecker','ScheduledChecker','PlaylistTools','ArtistSearcher','ImportExportManager',
                  'ImportExportMenu','ApplicationUI']
        with contextlib.ExitStack() as stack:
            for name in services:
                stack.enter_context(patch('anr.'+name))
            stack.enter_context(patch('anr.ConfigManager',return_value=manager))
            stack.enter_context(patch('anr.BridgeServer',return_value=bridge))
            stack.enter_context(patch('anr.SpotifyAuthManager',return_value=auth))
            stack.enter_context(patch('time.time',side_effect=[0,4]))
            app=Application()
            self.assertTrue(app.initialize())
        self.assertFalse(app._bridge_mode)
        self.assertIsInstance(app.spotify_api,SpotifyAPI)
        self.assertIsNone(app.bridge_server)
        bridge.stop.assert_called_once()
        auth.ensure_authenticated.assert_called_once()

    def test_oauth_builds_expected_credentials_and_scopes_without_opening_browser(self):
        manager=SimpleNamespace(config=Config(spotify_client_id='test-id',spotify_client_secret='test-secret'))
        auth=SpotifyAuthManager(manager)
        client=Mock()
        client.current_user.return_value={'id':'user','display_name':'Test'}
        with patch('anr.auth.SpotifyOAuth') as oauth, patch('anr.auth.spotipy.Spotify',return_value=client):
            self.assertTrue(auth.authenticate())
        args=oauth.call_args.kwargs
        self.assertEqual('test-id',args['client_id'])
        self.assertEqual('test-secret',args['client_secret'])
        self.assertIn('playlist-modify-private',args['scope'])
        self.assertEqual('http://127.0.0.1:8888/callback',args['redirect_uri'])


if __name__=='__main__':
    unittest.main()
