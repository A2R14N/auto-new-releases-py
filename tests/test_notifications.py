import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from anr.models import Profile, Config
from anr.bridge_api import BridgeAPI
from anr.notifications import notify_profile, render_notification
from tests.test_checker_safety import make_checker


class NotificationTests(unittest.TestCase):
    def test_fixed_messages_ignore_legacy_profile_customization(self):
        profile = Profile.from_dict({"id": "p", "name": "Muzică", "playlist_name": "Favorites",
                                     "spotify_notifications": False, "notification_duration": 9,
                                     "notification_messages": {"success": "Custom message", "no_new": ""}})
        loaded = Profile.from_dict(profile.to_dict())
        self.assertEqual("ANR: Added 12 songs to Favorites (0.0s).", render_notification(loaded, "success", SimpleNamespace(total_tracks_added=12)))
        self.assertEqual("ANR: No new releases for Muzică.", render_notification(loaded, "no_new"))
        self.assertNotIn("spotify_notifications", loaded.to_dict())
        self.assertNotIn("notification_duration", loaded.to_dict())
        self.assertNotIn("notification_messages", loaded.to_dict())

    def test_global_choice_persists_and_legacy_profile_flags_do_not_enable_it(self):
        legacy = {"profiles": [{"id": "a", "name": "A", "spotify_notifications": True},
                               {"id": "b", "name": "B", "spotify_notifications": False}]}
        config = Config.from_dict(legacy)
        self.assertFalse(config.spotify_notifications)
        self.assertFalse(Config().spotify_notifications)
        config.spotify_notifications = True
        loaded = Config.from_dict(config.to_dict())
        self.assertTrue(loaded.spotify_notifications)
        self.assertTrue(all("spotify_notifications" not in profile.to_dict() for profile in loaded.profiles))
        api = object.__new__(BridgeAPI)
        api.show_notification = Mock(return_value=True)
        for profile in loaded.profiles:
            self.assertTrue(notify_profile(api, profile, "no_new", enabled=loaded.spotify_notifications))
        self.assertEqual(2, api.show_notification.call_count)
        loaded.spotify_notifications = False
        for profile in loaded.profiles:
            self.assertFalse(notify_profile(api, profile, "no_new", enabled=loaded.spotify_notifications))
        self.assertEqual(2, api.show_notification.call_count)

    def test_profile_names_are_plain_text(self):
        profile = Profile(id="p", name="Muzică {playlist}")
        self.assertEqual("ANR: Checking Muzică {playlist} for new releases...", render_notification(profile, "start"))

    def test_disabled_and_delivery_failure_are_nonfatal(self):
        api = object.__new__(BridgeAPI)
        api.show_notification = Mock(return_value=True)
        profile = Profile(id="p", name="Test")
        self.assertFalse(notify_profile(api, profile, "success"))
        api.show_notification.assert_not_called()
        self.assertTrue(notify_profile(api, profile, "no_new", enabled=True))
        api.show_notification.assert_called_once_with("ANR: No new releases for Test.", False, 6000)
        api.show_notification.side_effect = RuntimeError("offline")
        with self.assertLogs("anr.notifications", level="WARNING"):
            self.assertFalse(notify_profile(api, profile, "success", enabled=True))

    def test_check_events_and_dry_run_suppression(self):
        checker, profile, _, _ = make_checker()
        checker.config_manager.config.spotify_notifications = True
        with patch("anr.notifications.notify_profile") as notify:
            checker.check_profile(profile, silent=True)
            self.assertEqual(["start", "success"], [call.args[2] for call in notify.call_args_list])
            self.assertTrue(all(call.kwargs["enabled"] for call in notify.call_args_list))
        checker, profile, _, _ = make_checker()
        with patch("anr.notifications.notify_profile") as notify:
            checker.check_profile(profile, silent=True, dry_run=True)
            notify.assert_not_called()
