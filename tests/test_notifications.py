import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from anr.models import Profile
from anr.bridge_api import BridgeAPI
from anr.notifications import notify_profile, render_notification
from tests.test_checker_safety import make_checker


class NotificationTests(unittest.TestCase):
    def test_fixed_messages_ignore_legacy_customization_and_keep_toggle(self):
        profile = Profile.from_dict({"id": "p", "name": "Muzică", "playlist_name": "Favorites",
                                     "spotify_notifications": False, "notification_duration": 9,
                                     "notification_messages": {"success": "Custom message", "no_new": ""}})
        loaded = Profile.from_dict(profile.to_dict())
        self.assertEqual("ANR: Added 12 songs to Favorites (0.0s).", render_notification(loaded, "success", SimpleNamespace(total_tracks_added=12)))
        self.assertEqual("ANR: No new releases for Muzică.", render_notification(loaded, "no_new"))
        self.assertFalse(loaded.spotify_notifications)
        self.assertNotIn("notification_duration", loaded.to_dict())
        self.assertNotIn("notification_messages", loaded.to_dict())
        self.assertFalse(Profile.from_dict({"id": "old", "name": "Old"}).spotify_notifications)
        self.assertFalse(Profile(id="new", name="New").spotify_notifications)
        enabled = Profile(id="enabled", name="Enabled", spotify_notifications=True)
        self.assertTrue(Profile.from_dict(enabled.to_dict()).spotify_notifications)

    def test_profile_names_are_plain_text(self):
        profile = Profile(id="p", name="Muzică {playlist}")
        self.assertEqual("ANR: Checking Muzică {playlist} for new releases...", render_notification(profile, "start"))

    def test_disabled_and_delivery_failure_are_nonfatal(self):
        api = object.__new__(BridgeAPI)
        api.show_notification = Mock(return_value=True)
        profile = Profile(id="p", name="Test", spotify_notifications=False)
        self.assertFalse(notify_profile(api, profile, "success"))
        api.show_notification.assert_not_called()
        profile.spotify_notifications = True
        self.assertTrue(notify_profile(api, profile, "no_new"))
        api.show_notification.assert_called_once_with("ANR: No new releases for Test.", False, 6000)
        api.show_notification.side_effect = RuntimeError("offline")
        with self.assertLogs("anr.notifications", level="WARNING"):
            self.assertFalse(notify_profile(api, profile, "success"))

    def test_check_events_and_dry_run_suppression(self):
        checker, profile, _, _ = make_checker()
        with patch("anr.notifications.notify_profile") as notify:
            checker.check_profile(profile, silent=True)
            self.assertEqual(["start", "success"], [call.args[2] for call in notify.call_args_list])
        checker, profile, _, _ = make_checker()
        with patch("anr.notifications.notify_profile") as notify:
            checker.check_profile(profile, silent=True, dry_run=True)
            notify.assert_not_called()
