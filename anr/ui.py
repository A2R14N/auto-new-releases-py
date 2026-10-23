"""
Simplified terminal user interface — flat menus, inline prompts, no builder/handler split.
"""

import os
import re
from datetime import datetime
from typing import Optional

from .constants import (
    APP_NAME, APP_VERSION, RICH_AVAILABLE, console,
    print_success, print_error, print_warning, print_info,
    parse_spotify_uri, clear_screen,
)
from .config import ConfigManager
from .profile import ProfileManager, ProfileMenu
from .views import (display_main_dashboard, display_profiles_dashboard,
                    display_menu as _print_menu, display_settings,
                    display_setting_editor, display_tracking_history)
from .settings import SETTING_BY_KEY

# Standalone helpers
def display_header():
    if RICH_AVAILABLE:
        from rich.text import Text
        t = Text()
        t.append(APP_NAME, style='anr.heading')
        t.append(f" v{APP_VERSION}", style='anr.secondary')
        console.print(t)
    else:
        print(f"\n{'─'*50}")
        print(f"  {APP_NAME} v{APP_VERSION}")
        print(f"{'─'*50}")


def display_status_bar(config_manager: ConfigManager):
    profile = config_manager.get_active_profile()
    playlist = profile.playlist_name or "Not set"
    last_check = "Never"
    if profile.last_check:
        last_check = datetime.fromtimestamp(profile.last_check).strftime("%Y-%m-%d %H:%M")

    if RICH_AVAILABLE:
        from rich.table import Table
        t = Table(show_header=False, box=None, padding=(0, 2),
                  width=min(68, console.width))
        t.add_column(style='anr.heading'); t.add_column(style='anr.text')
        t.add_column(style='anr.heading'); t.add_column(style='anr.text')
        t.add_row("Profile:", profile.name, "Artists:", str(len(profile.artists)))
        t.add_row("Playlist:", playlist[:35], "Last Check:", last_check)
        console.print(t)
        console.print()
    else:
        print(f"  Profile: {profile.name}  |  Artists: {len(profile.artists)}")
        print(f"  Playlist: {playlist[:40]}  |  Last Check: {last_check}")
        print()


def _prompt(message: str, default: str = "") -> str:
    if RICH_AVAILABLE:
        from rich.prompt import Prompt
        return Prompt.ask(message, default=default, console=console) if default else Prompt.ask(message, console=console)
    suffix = f" [{default}]: " if default else ": "
    result = input(message + suffix).strip()
    return result if result else default


def _confirm(message: str, default: bool = False) -> bool:
    if RICH_AVAILABLE:
        from rich.prompt import Confirm
        return Confirm.ask(message, default=default, console=console)
    suffix = " [Y/n]: " if default else " [y/N]: "
    result = input(message + suffix).strip().lower()
    return result in ('y', 'yes') if result else default


def _parse_artist_selection(value: str, artist_count: int) -> list:
    """Resolve displayed numbers and inclusive ranges before changing the list."""
    value = value.strip()
    if not value or value == '0':
        return []
    indices = set()
    for part in value.split(','):
        match = re.fullmatch(r'\s*([0-9]+)\s*(?:-\s*([0-9]+)\s*)?', part)
        if not match:
            raise ValueError('Use numbers or ranges, for example 1,2,3 or 1-5, 7-9.')
        start = int(match[1])
        end = int(match[2]) if match[2] is not None else start
        if not 1 <= start <= end <= artist_count:
            raise ValueError(f'Choose numbers from 1 to {artist_count}, with ranges in ascending order; use 0 alone to cancel.')
        indices.update(range(start, end + 1))
    return sorted(indices)


def _wait():
    input("\nPress Enter to continue")
    clear_screen()


def _choice(prompt: str = "Choice") -> str:
    try:
        if RICH_AVAILABLE:
            from rich.prompt import Prompt
            from rich.text import Text
            label = Text(prompt, style='anr.text')
            if '›' in prompt:
                index = prompt.index('›')
                label.stylize('anr.accent', index, index + 1)
            question = Prompt(label, console=console)
            question.prompt_suffix = ' '
            return question().strip().lower()
        return input(f"{prompt}: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return "0"


# Main UI class
class ApplicationUI:
    """Main application user interface — flat, simple, no sub-builders."""

    def __init__(self, config_manager: ConfigManager, profile_manager: ProfileManager):
        self.config_manager = config_manager
        self.profile_manager = profile_manager
        self.profile_menu = ProfileMenu(profile_manager)
        self._running = True
        self._app = None  # set by Application.initialize()

    def set_app(self, app):
        self._app = app

    @property
    def _profile(self):
        return self.config_manager.get_active_profile()

    # ── entry point ──────────────────────────────────────

    def run(self):
        clear_screen()
        display_header()
        app = self._app
        if app and app.playlist_restorer:
            app.playlist_restorer.check_and_offer_restore()

        while self._running:
            self._main_menu()

    # ── main menu ────────────────────────────────────────

    def _main_menu(self):
        clear_screen()

        profile = self._profile
        has_all = bool(profile.playlist_uri) and bool(profile.artists)
        n_profiles = len(self.config_manager.config.profiles)

        items = [
            ("1", "Check for New Releases",
             "ready" if has_all else "⚠ configure playlist + artists first"),
            ("2", "Check All Profiles", f"{sum(p.enabled for p in self.config_manager.config.profiles)} enabled"),
            None,
            ("3", "Artists", f"{len(profile.artists)} tracked"),
            ("4", "Playlist", profile.playlist_name or "not set"),
            None,
            ("5", "Profiles", f"{n_profiles} profiles"),
            ("6", "Settings", ""),
            ("7", "Import / Export", ""),
            None,
            ("s", "Schedule Status", ""),
            ("q", "Quit", ""),
        ]
        dashboard_rendered = display_main_dashboard(self.config_manager, has_all)
        if not dashboard_rendered:
            display_header()
            display_status_bar(self.config_manager)
            _print_menu("Main Menu", items, show_back=False, outlined=True)
        choice = _choice("Command ›" if RICH_AVAILABLE else "Choice")
        app = self._app

        if choice == "q":
            self._running = False
        elif choice == "1":
            if app and app.interactive_checker and has_all:
                clear_screen()
                app.interactive_checker.run_check()
                _wait()
            elif not has_all:
                print_warning("Configure a playlist and add artists first.")
                _wait()
        elif choice == "2":
            if app and app.interactive_checker:
                clear_screen()
                app.interactive_checker.run_check_all()
                _wait()
        elif choice == "3":
            self._menu_artists()
        elif choice == "4":
            self._menu_playlist()
        elif choice == "5":
            self._menu_profiles()
        elif choice == "6":
            self._menu_settings()
        elif choice == "7":
            self._menu_import_export()
        elif choice == "s":
            if app and app.scheduled_checker:
                clear_screen()
                app.scheduled_checker.display_schedule()
                _wait()

    # ── artists ──────────────────────────────────────────

    def _menu_artists(self):
        clear_screen()
        while True:
            profile = self._profile
            has = bool(profile.artists)
            app = self._app

            items = [
                ("1", "Add Artist", "search by name"),
                ("2", "Add by URL / URI", "paste Spotify link"),
                None,
                ("3", "Remove Artist", "") if has else ("3", "Remove Artist", "no artists"),
                ("4", "Clear All Artists", f"{len(profile.artists)} artists") if has else
                    ("4", "Clear All Artists", "no artists"),
                ("5", "Refresh Artist Data", "update followers") if has else
                    ("5", "Refresh Artist Data", "no artists"),
                None,
                ("l", "List Artists", f"{len(profile.artists)} tracked"),
            ]
            _print_menu("Artists", items, outlined=True, section_titles=("Add", "Manage", "Library"),
                        subtitle=f"{APP_NAME} / {profile.name} · {len(profile.artists)} tracked")
            choice = _choice("Command ›")

            if choice == "0":
                break
            elif choice == "1":
                if app and app.artist_searcher:
                    artist = app.artist_searcher.interactive_search()
                    if artist:
                        self.profile_manager.add_artist_to_profile(profile, artist)
            elif choice == "2":
                uri = _prompt("Spotify artist URL or URI")
                if uri:
                    artist_id = parse_spotify_uri(uri, "artist")
                    if not artist_id:
                        print_error("Invalid URL/URI")
                    elif app and app.spotify_api:
                        data = app.spotify_api.get_artist(f"spotify:artist:{artist_id}")
                        if data:
                            self.profile_manager.add_artist_to_profile(
                                profile, app.spotify_api.artist_to_model(data))
                        else:
                            print_error("Artist not found")
            elif choice == "3" and has:
                self._remove_artist()
            elif choice == "4" and has:
                if _confirm(f"Remove all {len(profile.artists)} artists?"):
                    self.profile_manager.clear_all_artists(profile)
            elif choice == "5" and has:
                self._refresh_artists()
            elif choice == "l":
                self.profile_manager.display_artists()
                _wait()

    def _remove_artist(self):
        profile = self._profile
        self.profile_manager.display_artists()
        while True:
            selection = _prompt(
                f"Numbers to remove (1-{len(profile.artists)}; e.g. 1,2,3 or 1-5, 7-9; 0=cancel)",
                default='0',
            )
            try:
                indices = _parse_artist_selection(selection, len(profile.artists))
            except ValueError as error:
                print_error(str(error))
                continue
            if not indices:
                return
            artists = [profile.artists[index - 1] for index in indices]
            if len(artists) == 1:
                confirmation = f"Remove '{artists[0].name}'?"
            else:
                print_info('Selected artists:')
                for index, artist in zip(indices, artists):
                    print_info(f'  [{index}] {artist.name}')
                confirmation = f'Remove these {len(artists)} artists from tracking?'
            if _confirm(confirmation):
                self.profile_manager.remove_artists_from_profile(profile, [artist.uri for artist in artists])
            return

    def _refresh_artists(self):
        app = self._app
        if not app:
            return
        profile = self._profile
        print_info(f"Refreshing {len(profile.artists)} artists")
        updated = 0
        for i, artist in enumerate(profile.artists):
            print(f"  [{i+1}/{len(profile.artists)}] {artist.name}")
            data = app.spotify_api.get_artist(artist.uri)
            if data:
                profile.artists[i] = app.spotify_api.artist_to_model(data)
                updated += 1
        self.config_manager.save()
        print_success(f"Updated {updated} artists")
        _wait()

    # ── playlist ─────────────────────────────────────────

    def _menu_playlist(self):
        """Merged playlist config + tools in one menu."""
        clear_screen()
        while True:
            profile = self._profile
            has = bool(profile.playlist_uri)
            app = self._app
            configured_playlists = sum(
                1 for item in self.config_manager.config.profiles if item.playlist_uri
            )

            items = [
                ("1", "Set Playlist", "choose from your library"),
                ("2", "Enter URL / URI", "paste Spotify link"),
                ("3", "Create New Playlist", ""),
            ]
            if has:
                items += [
                    None,
                    ("4", "View Playlist Stats", profile.playlist_name),
                    ("5", "Sort Playlist", "by date, popularity, name…"),
                    ("6", "Find & Remove Duplicates", ""),
                    ("7", "Analyze Playlist", "statistics"),
                    ("8", "Duplicate Playlist", "create a copy"),
                    ("9", "Remove Artist Tracks", "all tracks by one artist"),
                ]
            if configured_playlists:
                items += [
                    None,
                    ("a", "Sort All Profile Playlists", f"{configured_playlists} configured"),
                ]
            if has:
                items += [("x", "Clear Playlist Setting", "")]

            sections = ["Target playlist"]
            if has:
                sections.append("Tools")
            if configured_playlists:
                sections.append("Profiles")
            _print_menu("Playlist", items, outlined=True, section_titles=sections,
                        subtitle=f"{APP_NAME} / {profile.playlist_name or 'Not configured'}")
            choice = _choice("Command ›")

            if choice == "0":
                break
            elif choice == "1":
                if app and app.playlist_selector:
                    pl = app.playlist_selector.select_playlist()
                    if pl:
                        profile.playlist_uri = pl.get("uri", "")
                        profile.playlist_name = pl.get("name", "")
                        self.config_manager.save()
                        print_success(f"Set playlist: {profile.playlist_name}")
            elif choice == "2":
                uri = _prompt("Spotify playlist URL or URI")
                if uri:
                    pid = parse_spotify_uri(uri, "playlist")
                    if not pid:
                        print_error("Invalid URL/URI")
                    elif app and app.playlist_ops:
                        pl = app.playlist_ops.get_playlist_details(f"spotify:playlist:{pid}")
                        if pl:
                            profile.playlist_uri = pl.get("uri", "")
                            profile.playlist_name = pl.get("name", "")
                            self.config_manager.save()
                            print_success(f"Set playlist: {profile.playlist_name}")
                        else:
                            print_error("Playlist not found")
            elif choice == "3":
                name = _prompt("New playlist name")
                if name and app and app.playlist_ops:
                    pl = app.playlist_ops.create_playlist(name)
                    if pl:
                        profile.playlist_uri = pl.get("uri", "")
                        profile.playlist_name = pl.get("name", "")
                        self.config_manager.save()
                        print_success(f"Created: {profile.playlist_name}")
                    else:
                        print_error("Failed to create playlist")
            elif choice == "4" and has and app and app.playlist_ops:
                app.playlist_ops.display_playlist_stats(profile.playlist_uri)
                _wait()
            elif choice == "5" and has:
                self._sort_playlist(profile.playlist_uri, app)
            elif choice == "6" and has and app and app.playlist_tools:
                include_similar = _confirm("Include similar-name tracks?", default=False)
                app.playlist_tools.interactive_dedupe(profile.playlist_uri, include_similar)
                _wait()
            elif choice == "7" and has and app and app.playlist_tools:
                detailed = _confirm("Detailed analysis?", default=True)
                app.playlist_tools.analyzer.display_analysis(profile.playlist_uri, detailed)
                _wait()
            elif choice == "8" and has and app and app.playlist_tools:
                new_name = _prompt("Name for copy",
                                   default=f"{profile.playlist_name} (Copy)")
                if new_name:
                    result = app.playlist_tools.duplicate(profile.playlist_uri, new_name)
                    app.playlist_tools.duplicator.display_result(result)
                    _wait()
            elif choice == "9" and has and app:
                if app.artist_searcher:
                    artist = app.artist_searcher.interactive_search()
                    if artist and _confirm(f"Remove all tracks by '{artist.name}'?"):
                        removed, name = app.playlist_tools.remove_artist(
                            profile.playlist_uri, artist.uri)
                        if removed > 0:
                            print_success(f"Removed {removed} tracks by {name}")
                        else:
                            print_info(f"No tracks by {name}")
                        _wait()
            elif choice == "a" and configured_playlists and app and app.playlist_tools:
                if _confirm(
                    f"Sort all {configured_playlists} configured profile playlists by release date?"
                ):
                    self._sort_all_profile_playlists(app)
            elif choice == "x" and has:
                if _confirm(f"Clear playlist '{profile.playlist_name}'?"):
                    profile.playlist_uri = ""
                    profile.playlist_name = ""
                    self.config_manager.save()
                    print_success("Playlist setting cleared")

    def _sort_playlist(self, playlist_uri: str, app):
        if not app or not app.playlist_tools:
            return
        clear_screen()
        sorter = app.playlist_tools.sorter
        sorter.display_sort_options()
        try:
            if RICH_AVAILABLE:
                from rich.prompt import IntPrompt
                c = IntPrompt.ask("Sort option", default=1, console=console)
            else:
                c = int(input("Sort option [1]: ") or "1")
            sc = sorter.get_sort_from_choice(c)
            if sc:
                result = app.playlist_tools.sort(playlist_uri, sc[0], sc[1])
                if result.success:
                    print_success(f"Sorted {result.tracks_sorted} tracks")
                else:
                    print_error(f"Sort failed: {result.error_message}")
        except ValueError:
            print_error("Invalid input")
        _wait()

    def _sort_all_profile_playlists(self, app):
        profiles = [
            profile for profile in self.config_manager.config.profiles
            if profile.playlist_uri
        ]
        succeeded = 0
        total_tracks = 0
        for index, profile in enumerate(profiles, 1):
            print_info(
                f"[{index}/{len(profiles)}] Sorting {profile.name}: {profile.playlist_name}"
            )
            result = app.playlist_tools.sorter.sort_by_release_date(profile.playlist_uri)
            if result.success:
                succeeded += 1
                total_tracks += result.tracks_sorted
                print_success(f"  Sorted {result.tracks_sorted:,} tracks")
            else:
                print_error(f"  Failed: {result.error_message}")
        print()
        if succeeded == len(profiles):
            print_success(f"Sorted all {succeeded} playlists ({total_tracks:,} tracks)")
        else:
            print_warning(
                f"Sorted {succeeded}/{len(profiles)} playlists ({total_tracks:,} tracks)"
            )
        _wait()

    # ── profiles ─────────────────────────────────────────

    def _menu_profiles(self):
        while True:
            profiles = self.config_manager.config.profiles
            active = self._profile
            clear_screen()
            display_profiles_dashboard(self.config_manager)
            choice = _choice("Profile ›  number to switch")

            if choice == "0":
                break
            elif choice in {str(index) for index in range(1, len(profiles) + 1)}:
                self.profile_manager.switch_profile(profiles[int(choice) - 1].id)
            elif choice == "n":
                name = _prompt("New profile name")
                if name:
                    self.profile_manager.create_profile(name)
            elif choice == 'e':
                self.profile_manager.set_profile_enabled(active.id, not active.enabled)
            elif choice in ("r", "d", "x"):
                if choice == "r":
                    new_name = _prompt("New name", default=active.name)
                    if new_name and new_name != active.name:
                        self.profile_manager.rename_profile(active.id, new_name)
                elif choice == "d":
                    new_name = _prompt("Name for copy", default=f"{active.name} (Copy)")
                    if new_name:
                        self.profile_manager.duplicate_profile(active.id, new_name)
                elif len(profiles) <= 1:
                    print_warning("Cannot delete the only profile")
                elif _confirm(f"Delete '{active.name}'?"):
                    self.profile_manager.delete_profile(active.id)
            else:
                print_warning("Enter a profile number or N, R, D, X, E, 0")

    # ── settings ─────────────────────────────────────────

    def _menu_settings(self):
        """One visible control per preference, with focused editors."""
        feedback = None
        while True:
            clear_screen()
            self._display_settings_table(self._profile)
            if feedback:
                (print_error if feedback[1] == 'error' else print_success)(feedback[0])
            raw = _choice('Command ›  number to change · h history · 0 back')
            if raw in ("", "0"):
                break
            if raw in SETTING_BY_KEY:
                feedback = self._edit_setting(SETTING_BY_KEY[raw])
            elif raw == 'h':
                self._settings_history()
                feedback = None
            else:
                feedback = ('Choose a setting from 1 to 9, h for history, or 0 to go back.', 'error')

    def _display_settings_table(self, profile):
        display_settings(profile, self.config_manager.config.spotify_notifications)

    def _edit_setting(self, setting):
        error = ''
        while True:
            clear_screen()
            display_setting_editor(setting, self._profile, self.config_manager.config)
            if error:
                print_error(error)
            choice = _choice('Command ›')
            if choice in ('', '0'):
                return None
            if choice == 'c' and setting.minimum is not None:
                value = self._custom_setting_value(setting)
                if value is None:
                    error = ''
                    continue
            elif choice in {str(index) for index in range(1, len(setting.options) + 1)}:
                value = setting.options[int(choice) - 1][0]
            else:
                error = 'Choose one of the keys shown above.'
                continue
            return self._save_setting(setting, value)

    def _custom_setting_value(self, setting):
        error = ''
        while True:
            clear_screen()
            display_setting_editor(setting, self._profile, self.config_manager.config, custom=True)
            if error:
                print_error(error)
            try:
                raw = _prompt(setting.custom_label).strip()
            except (KeyboardInterrupt, EOFError):
                return None
            if not raw or raw.lower() in ('b', 'back'):
                return None
            try:
                value = int(raw)
                if setting.minimum <= value <= setting.maximum:
                    return value
            except ValueError:
                pass
            error = f'Enter a whole number from {setting.minimum} to {setting.maximum}, or b to go back.'

    def _save_setting(self, setting, value):
        profile = self._profile
        config = self.config_manager.config
        owner = config if setting.global_setting else profile
        fields = [setting.field] + ([setting.flag] if setting.flag else [])
        original = {field: getattr(owner, field) for field in fields}
        try:
            if setting.apply(profile, config, value):
                self.config_manager.save()
        except Exception as error:
            for field, old_value in original.items():
                setattr(owner, field, old_value)
            return (f'Could not save settings: {error}', 'error')
        return (f'{setting.label}: {setting.format(setting.current(profile, config))}', 'success')

    def _settings_history(self):
        feedback = ''
        while True:
            clear_screen()
            display_tracking_history(self._profile)
            if feedback:
                (print_error if feedback.startswith('Could not clear') else print_info)(feedback)
            choice = _choice('Command ›')
            if choice in ('', '0'):
                return
            if choice == 'c':
                feedback = self._reset_tracking()
            else:
                feedback = 'Choose c to clear history, or 0 to go back.'

    def _reset_tracking(self):
        profile = self._profile
        if not (profile.tracked_releases or profile.tracked_tracks or profile.pending_artists):
            return 'No tracking history to clear.'
        error = ''
        while True:
            clear_screen()
            display_tracking_history(profile, confirm=True)
            if error:
                print_error(error)
            try:
                answer = _prompt('Type CLEAR to confirm (Enter to cancel)').strip()
            except (KeyboardInterrupt, EOFError):
                return ''
            if answer in ('', '0'):
                return ''
            if answer == 'CLEAR':
                fields = ('tracked_releases', 'tracked_tracks', 'pending_artists', 'last_scan')
                original = {field: getattr(profile, field) for field in fields}
                try:
                    self.profile_manager.reset_tracked_releases(profile, mode='all')
                except Exception as error:
                    for field, value in original.items():
                        setattr(profile, field, value)
                    return f'Could not clear tracking history: {error}'
                return 'Tracking history cleared. Spotify playlist songs were kept.'
            error = 'Type CLEAR exactly, or press Enter to cancel.'

    # ── import / export ──────────────────────────────────

    def _menu_import_export(self):
        app = self._app
        if not app:
            return

        clear_screen()
        items = [
            ("1", "Export current profile", "complete backup"),
            ("2", "Export all profiles", "complete backup"),
            ("3", "Export artists only", "current profile"),
            None,
            ("4", "Import file", "JSON backup"),
            ("5", "Preview file", "no changes made"),
        ]
        _print_menu("Import / Export", items, outlined=True, section_titles=("Export", "Import"),
                    subtitle=f"{APP_NAME} / {self._profile.name}")
        choice = _choice("Command ›")

        if choice == "0":
            return
        elif choice == "1":
            app.import_export_menu.run_export_current()
            _wait()
        elif choice == "2":
            app.import_export_menu.run_export_all()
            _wait()
        elif choice == "3":
            app.import_export_menu.run_export_artists()
            _wait()
        elif choice == "4":
            app.import_export_menu.run_import()
            _wait()
        elif choice == "5":
            fp = _prompt("File path")
            if fp:
                app.import_export_manager.display_preview(fp)
                _wait()
