import unittest
from types import SimpleNamespace

from anr.cli import CLIHandler, create_argument_parser


class AllProfileSortTests(unittest.TestCase):
    def test_parser_accepts_playlist_sort_all(self):
        args = create_argument_parser().parse_args(["playlist", "sort", "--all"])
        self.assertEqual("playlist", args.command)
        self.assertEqual("sort", args.playlist_command)
        self.assertTrue(args.all)

    def test_all_profile_sort_skips_unconfigured_and_reports_failure(self):
        profiles = [
            SimpleNamespace(name="One", playlist_name="One list", playlist_uri="one"),
            SimpleNamespace(name="Missing", playlist_name="", playlist_uri=""),
            SimpleNamespace(name="Two", playlist_name="Two list", playlist_uri="two"),
        ]
        results = iter([
            SimpleNamespace(success=True, tracks_sorted=10, error_message=""),
            SimpleNamespace(success=False, tracks_sorted=0, error_message="failed"),
        ])
        calls = []

        def sort(uri):
            calls.append(uri)
            return next(results)

        app = SimpleNamespace(
            config_manager=SimpleNamespace(config=SimpleNamespace(profiles=profiles)),
            playlist_tools=SimpleNamespace(
                sorter=SimpleNamespace(sort_by_release_date=sort)
            ),
        )
        handler = CLIHandler(app)
        handler.log = lambda *_args, **_kwargs: None

        self.assertEqual(1, handler._sort_all_profile_playlists())
        self.assertEqual(["one", "two"], calls)


if __name__ == "__main__":
    unittest.main()
