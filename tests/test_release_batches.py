import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from anr.api import ReleaseFetcher, SpotifyAPIError
from anr.bridge_api import BridgeAPI
from anr.checker import CheckStatus, ReleaseChecker
from anr.models import Artist, Config, Profile


class BatchAPI:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.batches = []
        self.singles = []

    def releases(self, uri):
        artist_id = uri.split(":")[-1]
        return [{"uri": f"spotify:album:{artist_id}", "name": artist_id,
                 "release_date": date.today().isoformat()}]

    def get_artist_albums_batch(self, uris, include_groups="album,single"):
        self.batches.append(list(uris))
        if self.mode == "old_bridge":
            raise SpotifyAPIError("Unknown method: get_artist_albums_batch")
        return {uri: {"releases": self.releases(uri) if not (self.mode == "all_errors" or (self.mode == "one_error" and uri.endswith(":b"))) else [],
                      "error": "429" if self.mode == "all_errors" or (self.mode == "one_error" and uri.endswith(":b")) else None}
                for uri in uris}

    def get_artist_albums(self, uri, include_groups="album,single"):
        self.singles.append(uri)
        return self.releases(uri)

    def get_album(self, uri):
        artist_id = uri.split(":")[-1]
        if self.mode == "album_error" and artist_id == "b":
            raise SpotifyAPIError("Incomplete album tracks")
        return {"tracks": {"items": [
            {"uri": f"spotify:track:{artist_id}", "name": artist_id, "artists": []},
            {"uri": "spotify:track:shared", "name": "Shared", "artists": []},
        ]}}


def make_checker(mode="ok"):
    api = BatchAPI(mode)
    profile = Profile(id="test", name="Test", sort_by_date=False,
                      playlist_uri="spotify:playlist:test",
                      artists=[Artist(uri=f"spotify:artist:{name}", name=name) for name in "abcdef"])
    calls = []

    def add(_uri, tracks):
        calls.append(list(tracks))
        return len(tracks), 0

    ops = SimpleNamespace(get_playlist_tracks=lambda _uri: [], add_tracks=add)
    config = SimpleNamespace(config=Config(), save=lambda: None)
    return ReleaseChecker(api, ops, config), api, profile, calls


class ArtistBatchTests(unittest.TestCase):
    def test_batch_preserves_artist_order_and_duplicate_filtering(self):
        checker, api, profile, calls = make_checker()
        progress = []
        result = checker.check_profile(profile, silent=True, progress_callback=progress.append)
        self.assertEqual(CheckStatus.SUCCESS, result.status)
        self.assertEqual([4, 2], [len(batch) for batch in api.batches])
        self.assertEqual([], api.singles)
        self.assertEqual([["spotify:track:a", "spotify:track:shared"] +
                          [f"spotify:track:{name}" for name in "bcdef"]], calls)
        self.assertEqual(list("abcdef"), [p.artist_name for p in progress if p.phase == "artist"])
        self.assertEqual(6, result.artists_checked)

    def test_individual_error_stays_retryable_while_other_artists_complete(self):
        checker, _api, profile, calls = make_checker("one_error")
        profile.last_check = 123
        with patch('anr.checker.print_warning') as warning:
            result = checker.check_profile(profile, silent=True)
        self.assertEqual(CheckStatus.PARTIAL, result.status)
        self.assertIn('Could not check 1 of 6 artists', result.error_message)
        warning.assert_called_once_with('    Could not check b: 429')
        self.assertEqual(123, profile.last_check)
        self.assertEqual(6, result.artists_checked)
        self.assertEqual(1, len([r for r in result.artist_results if r.status == CheckStatus.ERROR]))
        self.assertNotIn("spotify:album:b", profile.tracked_releases)
        self.assertNotIn("spotify:track:b", profile.tracked_tracks)
        self.assertIn("spotify:album:f", profile.tracked_releases)
        self.assertNotIn("spotify:track:b", calls[0])

    def test_all_artist_errors_cannot_report_up_to_date_or_advance_last_check(self):
        checker, _api, profile, calls = make_checker('all_errors')
        with patch('anr.checker.print_warning'):
            result = checker.check_profile(profile, silent=True)
        self.assertEqual(CheckStatus.ERROR, result.status)
        self.assertIn('Could not check 6 of 6 artists', result.error_message)
        self.assertEqual([], calls)
        self.assertEqual({}, profile.tracked_releases)
        self.assertEqual({}, profile.tracked_tracks)
        self.assertIsNone(profile.last_check)

    def test_retry_after_artist_error_does_not_repeat_successful_additions(self):
        checker, api, profile, calls = make_checker('one_error')
        with patch('anr.checker.print_warning'):
            first = checker.check_profile(profile, silent=True)
        self.assertEqual(CheckStatus.PARTIAL, first.status)
        api.mode = 'ok'
        second = checker.check_profile(profile, silent=True)
        self.assertEqual(CheckStatus.SUCCESS, second.status)
        self.assertEqual(['spotify:track:b'], calls[1])
        self.assertIsNotNone(profile.last_check)

    def test_old_bridge_falls_back_to_existing_single_artist_reads(self):
        checker, api, profile, calls = make_checker("old_bridge")
        result = checker.check_profile(profile, silent=True)
        self.assertEqual(CheckStatus.SUCCESS, result.status)
        self.assertEqual([artist.uri for artist in profile.artists], api.singles)
        self.assertEqual(7, len(calls[0]))

    def test_batched_dry_run_keeps_tracking_and_playlist_untouched(self):
        checker, api, profile, calls = make_checker()
        result = checker.check_profile(profile, silent=True, dry_run=True)
        self.assertEqual(7, result.total_tracks_added)
        self.assertEqual(2, len(api.batches))
        self.assertEqual([], calls)
        self.assertEqual({}, profile.tracked_releases)
        self.assertEqual({}, profile.tracked_tracks)
        self.assertIsNone(profile.last_check)

    def test_incomplete_album_cannot_commit_a_partially_checked_release(self):
        checker, _api, profile, calls = make_checker("album_error")
        with patch("anr.checker.traceback.print_exc"):
            result = checker.check_profile(profile, silent=True)
        self.assertEqual(CheckStatus.ERROR, result.status)
        self.assertEqual([], calls)
        self.assertEqual({}, profile.tracked_releases)
        self.assertEqual({}, profile.tracked_tracks)

    def test_non_bridge_fetcher_retains_single_artist_path(self):
        fetcher = ReleaseFetcher(SimpleNamespace(get_artist_albums=lambda uri, **_: [{"uri": uri}]))
        self.assertEqual({}, fetcher.get_artist_releases_batch(["spotify:artist:a"]))
        self.assertEqual([{"uri": "spotify:artist:a"}], fetcher.get_artist_releases("spotify:artist:a"))

    def test_bridge_batch_validates_response_before_checker_uses_it(self):
        api = object.__new__(BridgeAPI)
        calls = []
        good = {"spotify:artist:a": {"releases": [], "error": None}}

        def call(method, params):
            calls.append((method, params))
            return good

        api._call = call
        self.assertEqual(good, api.get_artist_albums_batch(["spotify:artist:a"]))
        self.assertEqual("get_artist_albums_batch", calls[0][0])
        self.assertEqual(["a"], calls[0][1]["artist_ids"])
        for bad in [None, {}, {"spotify:artist:a": []},
                    {"spotify:artist:a": {"releases": [], "error": 42}},
                    {"spotify:artist:a": {"releases": [], "error": ""}},
                    {"spotify:artist:a": {"releases": []}}]:
            api._call = lambda *_args, response=bad: response
            with self.assertRaises(SpotifyAPIError):
                api.get_artist_albums_batch(["spotify:artist:a"])


if __name__ == "__main__":
    unittest.main()
