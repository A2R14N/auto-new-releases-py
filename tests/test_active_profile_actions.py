import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

from anr.models import Config, Profile
from anr.profile import ProfileManager
from anr.ui import ApplicationUI


class ActiveProfileActionsTests(unittest.TestCase):
    def setUp(self):
        self.first = Profile('first', 'First')
        self.active = Profile('active', 'Active')
        self.config = SimpleNamespace(config=Config(profiles=[self.first, self.active], active_profile_id='active'),
            save=Mock())
        self.config.get_active_profile = lambda: next(
            p for p in self.config.config.profiles if p.id == self.config.config.active_profile_id)
        self.config.get_profile_by_id = lambda pid: next(
            (p for p in self.config.config.profiles if p.id == pid), None)
        self.manager = ProfileManager(self.config)
        self.ui = ApplicationUI(self.config, self.manager)

    def drive(self, commands, prompts=(), confirm=False):
        with ExitStack() as stack:
            for name in ('clear_screen', 'display_profiles_dashboard', 'print_warning'):
                stack.enter_context(patch('anr.ui.' + name))
            stack.enter_context(patch('anr.profile.print_success'))
            choices = stack.enter_context(patch('anr.ui._choice', side_effect=commands))
            prompt = stack.enter_context(patch('anr.ui._prompt', side_effect=prompts))
            confirmation = stack.enter_context(patch('anr.ui._confirm', return_value=confirm))
            self.ui._menu_profiles()
        return choices, prompt, confirmation

    def test_rename_uses_active_profile_without_another_selection(self):
        choices, prompt, _ = self.drive(['r', '0'], ['Renamed'])
        self.assertEqual(2, choices.call_count)
        prompt.assert_called_once_with('New name', default='Active')
        self.assertEqual('Renamed', self.active.name)
        self.assertEqual('First', self.first.name)
        self.assertEqual('active', self.config.config.active_profile_id)

    def test_duplicate_uses_active_profile_and_activates_the_copy(self):
        self.active.check_interval = 12
        choices, prompt, _ = self.drive(['d', '0'], ['Copied'])
        self.assertEqual(2, choices.call_count)
        prompt.assert_called_once_with('Name for copy', default='Active (Copy)')
        copy = self.config.get_active_profile()
        self.assertEqual('Copied', copy.name)
        self.assertEqual(12, copy.check_interval)
        self.assertEqual(3, len(self.config.config.profiles))

    def test_delete_targets_active_profile_and_still_requires_confirmation(self):
        for confirm in (False, True):
            with self.subTest(confirm=confirm):
                choices, _, confirmation = self.drive(['x', '0'], confirm=confirm)
                self.assertEqual(2, choices.call_count)
                confirmation.assert_called_once_with("Delete 'Active'?")
                self.assertEqual(1 if confirm else 2, len(self.config.config.profiles))
        self.assertEqual('first', self.config.config.active_profile_id)

    def test_switching_then_toggling_uses_the_new_active_profile(self):
        choices, prompt, _ = self.drive(['1', 'e', '0'])
        self.assertEqual(3, choices.call_count)
        prompt.assert_not_called()
        self.assertFalse(self.first.enabled)
        self.assertTrue(self.active.enabled)
        self.assertEqual('first', self.config.config.active_profile_id)

    def test_cannot_delete_only_profile(self):
        self.config.config.profiles = [self.active]
        _, _, confirmation = self.drive(['x', '0'], confirm=True)
        confirmation.assert_not_called()
        self.config.save.assert_not_called()
        self.assertEqual([self.active], self.config.config.profiles)


if __name__ == '__main__':
    unittest.main()
