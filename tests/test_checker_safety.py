import unittest
from types import SimpleNamespace
from unittest.mock import patch

from anr.checker import CheckStatus, ReleaseChecker
from anr.models import Artist, Profile


class FakeAPI:
    def get_album(self, _uri):
        return {
            "popularity": 50,
            "tracks": {
                "items": [
                    {"uri": "spotify:track:one", "name": "One", "artists": []},
                    {"uri": "spotify:track:two", "name": "Two", "artists": []},
                ]
            },
        }


class FakePlaylistOps:
    def __init__(self, add_result=(2, 0)):
        self.add_result = add_result
        self.add_calls = []

    def get_playlist_tracks(self, _uri):
        return []

    def add_tracks(self, playlist_uri, tracks):
        self.add_calls.append((playlist_uri, list(tracks)))
        return self.add_result


class FakeConfigManager:
    def __init__(self):
        self.save_count = 0

    def save(self):
        self.save_count += 1


def make_checker(add_result=(2, 0), sort_by_date=False):
    profile = Profile(
        id="profile",
        name="Test",
        artists=[Artist(uri="spotify:artist:test", name="Artist")],
        playlist_uri="spotify:playlist:test",
        playlist_name="Test playlist",
        sort_by_date=sort_by_date,
    )
    ops = FakePlaylistOps(add_result)
    config = FakeConfigManager()
    checker = ReleaseChecker(FakeAPI(), ops, config)
    checker.release_fetcher = SimpleNamespace(
        get_artist_releases=lambda _uri: [
            {"uri": "spotify:album:new", "name": "New", "release_date": "2026-08-25"}
        ]
    )
    return checker, profile, ops, config


class ReleaseCheckerSafetyTests(unittest.TestCase):
    def test_partial_add_remains_retryable(self):
        checker, profile, _ops, config = make_checker(add_result=(1, 1))

        result = checker.check_profile(profile, silent=True)

        self.assertEqual(CheckStatus.PARTIAL, result.status)
        self.assertNotIn("spotify:album:new", profile.tracked_releases)
        self.assertNotIn("spotify:track:one", profile.tracked_tracks)
        self.assertNotIn("spotify:track:two", profile.tracked_tracks)
        self.assertEqual(1, config.save_count)

    def test_complete_add_commits_tracking_history(self):
        checker, profile, _ops, config = make_checker()

        result = checker.check_profile(profile, silent=True)

        self.assertEqual(CheckStatus.SUCCESS, result.status)
        self.assertIn("spotify:album:new", profile.tracked_releases)
        self.assertIn("spotify:track:one", profile.tracked_tracks)
        self.assertIn("spotify:track:two", profile.tracked_tracks)
        self.assertEqual(1, config.save_count)

    def test_dry_run_discovers_without_mutating(self):
        checker, profile, ops, config = make_checker()

        result = checker.check_profile(profile, silent=True, dry_run=True)

        self.assertEqual(2, result.total_tracks_added)
        self.assertEqual([], ops.add_calls)
        self.assertEqual({}, profile.tracked_releases)
        self.assertEqual({}, profile.tracked_tracks)
        self.assertIsNone(profile.last_check)
        self.assertEqual(0, config.save_count)

    def test_sort_failure_marks_check_partial(self):
        checker, profile, _ops, _config = make_checker(sort_by_date=True)
        sorter = SimpleNamespace(
            sort_by_release_date=lambda _uri: SimpleNamespace(
                success=False, error_message="rewrite failed"
            )
        )
        tools = SimpleNamespace(sorter=sorter)

        with patch("anr.tools.PlaylistTools", return_value=tools):
            result = checker.check_profile(profile, silent=True)

        self.assertEqual(CheckStatus.PARTIAL, result.status)
        self.assertIn("rewrite failed", result.error_message)


if __name__ == "__main__":
    unittest.main()
