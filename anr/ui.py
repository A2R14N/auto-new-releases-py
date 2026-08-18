"""
Simplified terminal user interface — flat menus, inline prompts, no builder/handler split.
"""

import os
from datetime import datetime
from typing import Optional

from .constants import (
    APP_NAME, APP_VERSION, RICH_AVAILABLE, console,
    print_success, print_error, print_warning, print_info,
    parse_spotify_uri, clear_screen,
)
from .config import ConfigManager
from .profile import ProfileManager, ProfileMenu

# Standalone helpers
def display_header():
    if RICH_AVAILABLE:
        from rich.panel import Panel
        from rich.text import Text
        t = Text()
        t.append("♪ ", style="bold green")
        t.append(APP_NAME, style="bold white")
        t.append(f" v{APP_VERSION}", style="dim")
        console.print(Panel(t, border_style="green", padding=(0, 2)))
    else:
        print(f"\n{'─'*50}")
        print(f"  ♪ {APP_NAME} v{APP_VERSION}")
        print(f"{'─'*50}")


def display_status_bar(config_manager: ConfigManager):
    profile = config_manager.get_active_profile()
    playlist = profile.playlist_name or "Not set"
    last_check = "Never"
    if profile.last_check:
        last_check = datetime.fromtimestamp(profile.last_check).strftime("%Y-%m-%d %H:%M")

    if RICH_AVAILABLE:
        from rich.table import Table
        t = Table(show_header=False, box=None, padding=(0, 2))
        t.add_column(style="bold cyan"); t.add_column(style="white")
        t.add_column(style="bold cyan"); t.add_column(style="white")
        t.add_row("Profile:", profile.name, "Artists:", str(len(profile.artists)))
        t.add_row("Playlist:", playlist[:35], "Last Check:", last_check)
        console.print(t)
        console.print()
    else:
        print(f"  Profile: {profile.name}  |  Artists: {len(profile.artists)}")
        print(f"  Playlist: {playlist[:40]}  |  Last Check: {last_check}")
        print()


def display_main_dashboard(config_manager: ConfigManager, ready: bool):
    """Render the compact structured dashboard used by the main menu."""
    profile = config_manager.get_active_profile()
    profiles = config_manager.config.profiles
    playlist = profile.playlist_name or "Not configured"
    last_check = "Never"
    if profile.last_check:
        last_check = datetime.fromtimestamp(profile.last_check).strftime("%Y-%m-%d %H:%M")

    if not RICH_AVAILABLE:
        display_header()
        display_status_bar(config_manager)
        return False

    from rich import box
    from rich.console import Group
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text

    profile_row = Table.grid(expand=True)
    profile_row.add_column()
    profile_row.add_column(justify="right")
    profile_row.add_row(
        Text(profile.name, style="bold #38bdf8"),
        Text("● Ready" if ready else "● Setup required",
             style="bold #1ed760" if ready else "bold yellow"),
    )
    profile_row.add_row(
        Text(f"{len(profile.artists)} artists · {playlist}", style="dim"),
        Text(last_check, style="dim"),
    )

    actions = Table.grid(padding=(0, 1), expand=True)
    actions.add_column(width=4)
    actions.add_column()
    actions.add_column(justify="right", style="dim")

    def add_action(key: str, label: str, detail: str = "", selected: bool = False):
        key_text = Text(f">{key}" if selected else f" {key}",
                        style="bold #38bdf8" if selected else "bold #7dd3fc")
        label_text = Text(label, style="bold white" if selected else "white")
        detail_text = Text(detail, style="#1ed760" if selected and ready else "dim")
        actions.add_row(key_text, label_text, detail_text)

    add_action("1", "Check current profile", "Ready" if ready else "Setup required", True)
    add_action("2", "Check all profiles", f"{len(profiles)} profiles")
    add_action("3", "Manage artists", f"{len(profile.artists)} tracked")
    add_action("4", "Manage playlist", playlist)
    add_action("5", "Manage profiles", f"{len(profiles)} profiles")
    add_action("6", "Settings")

    title = Text(APP_NAME, style="bold white")
    title.append(f"  v{APP_VERSION}", style="dim")
    footer = Text()
    footer.append(" [S]", style="bold #7dd3fc")
    footer.append(" Schedule     ", style="dim")
    footer.append("[7]", style="bold #7dd3fc")
    footer.append(" Import / Export     ", style="dim")
    footer.append("[Q]", style="bold #7dd3fc")
    footer.append(" Quit", style="dim")

    body = Group(
        profile_row,
        Rule("Actions", style="#38bdf8"),
        actions,
        Rule(style="dim", characters="-"),
        footer,
    )
    panel_width = min(68, max(54, console.size.width))
    console.print(Panel(
        body,
        title=title,
        width=panel_width,
        box=box.ROUNDED,
        border_style="#5f87d7",
        padding=(1, 2),
    ))
    return True


def display_profiles_dashboard(config_manager: ConfigManager):
    """Render profile selection and profile actions in a single compact view."""
    profiles = config_manager.config.profiles

    if not RICH_AVAILABLE:
        for index, profile in enumerate(profiles, 1):
            active = " [Active]" if profile.id == config_manager.config.active_profile_id else ""
            print(f"  {index}. {profile.name} - {len(profile.artists)} artists - "
                  f"{profile.playlist_name or 'Not set'}{active}")
        print("\n  [N] New  [R] Rename  [D] Duplicate  [X] Delete  [0] Back\n")
        return

    from rich import box
    from rich.console import Group
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text

    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(width=3, justify="right")
    table.add_column(width=17, overflow="ellipsis", no_wrap=True)
    table.add_column(width=11, justify="right", no_wrap=True)
    table.add_column(ratio=1, overflow="ellipsis", no_wrap=True)
    table.add_column(width=7, justify="right")

    for index, profile in enumerate(profiles, 1):
        active = profile.id == config_manager.config.active_profile_id
        artist_word = "artist" if len(profile.artists) == 1 else "artists"
        table.add_row(
            Text(str(index), style="bold #38bdf8" if active else "bold #7dd3fc"),
            Text(profile.name, style="bold #38bdf8" if active else "white"),
            Text(f"{len(profile.artists)} {artist_word}", style="dim"),
            Text(profile.playlist_name or "Not configured", style="dim"),
            Text("Active" if active else "", style="bold #1ed760"),
        )

    footer = Text()
    for key, label in (("N", "New"), ("R", "Rename"), ("D", "Duplicate"),
                       ("X", "Delete"), ("0", "Back")):
        if footer:
            footer.append("   ")
        footer.append(f"[{key}]", style="bold #7dd3fc")
        footer.append(f" {label}", style="dim")

    body = Group(table, Rule(style="dim", characters="-"), footer)
    panel_width = min(72, max(58, console.size.width))
    console.print(Panel(
        body,
        title=Text(f"Profiles  ·  {len(profiles)}", style="bold white"),
        width=panel_width,
        box=box.ROUNDED,
        border_style="#5f87d7",
        padding=(1, 2),
    ))


def _prompt(message: str, default: str = "") -> str:
    if RICH_AVAILABLE:
        from rich.prompt import Prompt
        return Prompt.ask(message, default=default) if default else Prompt.ask(message)
    suffix = f" [{default}]: " if default else ": "
    result = input(message + suffix).strip()
    return result if result else default


def _confirm(message: str, default: bool = False) -> bool:
    if RICH_AVAILABLE:
        from rich.prompt import Confirm
        return Confirm.ask(message, default=default)
    suffix = " [Y/n]: " if default else " [y/N]: "
    result = input(message + suffix).strip().lower()
    return result in ('y', 'yes') if result else default


def _wait():
    input("\nPress Enter to continue...")


def _print_menu(title: str, items: list, show_back: bool = True):
    """
    Print a simple menu.
    items: list of (key, label, hint) tuples; use None for a separator.
    """
    if RICH_AVAILABLE:
        from rich import box
        from rich.console import Group
        from rich.panel import Panel
        from rich.rule import Rule
        from rich.text import Text

        sections = []
        rows = []

        def flush_rows():
            if rows:
                sections.extend(rows)
                rows.clear()

        for item in items:
            if item is None:
                flush_rows()
                sections.append(Rule(style="dim", characters="-"))
            else:
                key, label, hint = item
                if key:
                    line = Text(" ")
                    line.append(f"{key:>2}", style="bold #7dd3fc")
                    line.append("       ")
                    line.append(label, style="white")
                    if hint:
                        padding = max(2, 48 - len(label) - len(hint))
                        line.append(" " * padding)
                        hint_style = "yellow" if "disabled" in hint or "no " in hint else "dim"
                        line.append(hint, style=hint_style)
                    rows.append(line)
        flush_rows()

        if show_back:
            sections.append(Rule(style="dim", characters="-"))
            back = Text("  0", style="bold #7dd3fc")
            back.append("       Back", style="dim")
            sections.append(back)

        panel_width = min(68, max(54, console.size.width))
        console.print(Panel(
            Group(*sections),
            title=Text(title, style="bold white"),
            width=panel_width,
            box=box.ROUNDED,
            border_style="#5f87d7",
            padding=(1, 2),
        ))
    else:
        print(f"\n{'='*52}\n  {title}\n{'='*52}")
        for item in items:
            if item is None:
                print(f"  {'─'*44}")
            else:
                key, label, hint = item
                if key:
                    desc = f"  ({hint})" if hint else ""
                    print(f"  {key:>2}. {label}{desc}")
        if show_back:
            print(f"\n   0. Back")
        print()


def _choice(prompt: str = "Choice") -> str:
    try:
        if RICH_AVAILABLE:
            from rich.prompt import Prompt
            return Prompt.ask(prompt).strip().lower()
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
            ("2", "Check All Profiles", f"{n_profiles} profiles"),
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
            _print_menu("Main Menu", items, show_back=False)
        choice = _choice("Command ›" if RICH_AVAILABLE else "Choice")
        app = self._app

        if choice == "q":
            self._running = False
        elif choice == "1":
            if app and app.interactive_checker and has_all:
                app.interactive_checker.run_check()
                _wait()
            elif not has_all:
                print_warning("Configure a playlist and add artists first.")
                _wait()
        elif choice == "2":
            if app and app.interactive_checker:
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
                app.scheduled_checker.display_schedule()
                _wait()

    # ── artists ──────────────────────────────────────────

    def _menu_artists(self):
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
            _print_menu(f"Artists  ·  {len(profile.artists)} tracked", items)
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
        try:
            if RICH_AVAILABLE:
                from rich.prompt import IntPrompt
                idx = IntPrompt.ask(f"Number to remove (1-{len(profile.artists)}, 0=cancel)")
            else:
                idx = int(input("Number to remove (0=cancel): ") or "0")
            if idx == 0:
                return
            artist = self.profile_manager.find_artist_by_index(profile, idx)
            if artist and _confirm(f"Remove '{artist.name}'?"):
                self.profile_manager.remove_artist_from_profile(profile, artist.uri)
            elif not artist:
                print_error("Invalid number")
        except ValueError:
            print_error("Invalid input")

    def _refresh_artists(self):
        app = self._app
        if not app:
            return
        profile = self._profile
        print_info(f"Refreshing {len(profile.artists)} artists...")
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
        while True:
            profile = self._profile
            has = bool(profile.playlist_uri)
            app = self._app

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
                    None,
                    ("x", "Clear Playlist Setting", ""),
                ]

            _print_menu("Playlist", items)
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
            elif choice == "x" and has:
                if _confirm(f"Clear playlist '{profile.playlist_name}'?"):
                    profile.playlist_uri = ""
                    profile.playlist_name = ""
                    self.config_manager.save()
                    print_success("Playlist setting cleared")

    def _sort_playlist(self, playlist_uri: str, app):
        if not app or not app.playlist_tools:
            return
        sorter = app.playlist_tools.sorter
        sorter.display_sort_options()
        try:
            if RICH_AVAILABLE:
                from rich.prompt import IntPrompt
                c = IntPrompt.ask("Sort option", default=1)
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

    # ── profiles ─────────────────────────────────────────

    def _menu_profiles(self):
        while True:
            profiles = self.config_manager.config.profiles
            clear_screen()
            display_profiles_dashboard(self.config_manager)
            choice = _choice("Profile ›  number to switch")

            if choice == "0":
                break
            elif choice.isdigit() and 1 <= int(choice) <= len(profiles):
                self.profile_manager.switch_profile(profiles[int(choice) - 1].id)
            elif choice == "n":
                name = _prompt("New profile name")
                if name:
                    self.profile_manager.create_profile(name)
            elif choice in ("r", "d", "x"):
                action = {"r": "rename", "d": "duplicate", "x": "delete"}[choice]
                target = self._select_profile_for_action(profiles, action)
                if not target:
                    continue
                if choice == "r":
                    new_name = _prompt("New name", default=target.name)
                    if new_name and new_name != target.name:
                        self.profile_manager.rename_profile(target.id, new_name)
                elif choice == "d":
                    new_name = _prompt("Name for copy", default=f"{target.name} (Copy)")
                    if new_name:
                        self.profile_manager.duplicate_profile(target.id, new_name)
                elif len(profiles) <= 1:
                    print_warning("Cannot delete the only profile")
                elif _confirm(f"Delete '{target.name}'?"):
                    self.profile_manager.delete_profile(target.id)
            else:
                print_warning("Enter a profile number or N, R, D, X, 0")

    def _select_profile_for_action(self, profiles, action: str):
        """Select from the profile list already visible without printing it again."""
        choice = _choice(f"Profile to {action}  1-{len(profiles)} · 0 cancel")
        if choice == "0":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(profiles):
            return profiles[int(choice) - 1]
        print_warning("Invalid profile number")
        return None

    # ── settings ─────────────────────────────────────────

    def _menu_settings(self):
        """Compact inline settings toggle — no numbered items, just type a letter."""
        while True:
            profile = self._profile
            self._display_settings_table(profile)

            raw = _choice("Command ›  letter to change · 0 to go back")

            if raw in ("", "0"):
                break
            elif raw == "i":
                self._change_int_setting("check_interval", "Check interval (hours)", 1, 168)
            elif raw == "d":
                self._change_int_setting("days_to_check", "Days to check (0 = all time)", 0, 365)
            elif raw == "s":
                self._toggle("sort_by_date", "Sort by date")
            elif raw == "r":
                self._toggle("skip_remixes", "Skip remixes")
            elif raw == "p":
                self._toggle("skip_low_popularity", "Skip low popularity")
            elif raw == "a":
                self._toggle("skip_long_albums", "Skip long albums")
            elif raw == "l":
                self._toggle("limit_songs_per_album", "Limit songs per album")
            elif raw == "m":
                self._change_int_setting("min_popularity", "Min popularity (0-100)", 0, 100)
            elif raw == "x":
                self._change_int_setting("max_songs_per_album", "Max songs per album", 1, 50)
            elif raw == "u":
                self._toggle("skip_similar_duplicates", "Skip similar duplicates")
            elif raw == "reset":
                self._reset_tracking()
            else:
                print_warning("Unknown key — see table above")

    def _display_settings_table(self, profile):
        yn = lambda v: "[green]Yes[/]" if RICH_AVAILABLE and v else ("Yes" if v else "No")
        days = profile.days_to_check if profile.days_to_check > 0 else "All"
        release_count = len(profile.tracked_releases)
        track_count = len(getattr(profile, "tracked_tracks", {}))

        if RICH_AVAILABLE:
            from rich import box
            from rich.console import Group
            from rich.panel import Panel
            from rich.rule import Rule
            from rich.table import Table
            from rich.text import Text

            t = Table.grid(expand=True, padding=(0, 1))
            t.add_column(width=5, style="bold #7dd3fc")
            t.add_column(style="white")
            t.add_column(justify="right")

            def setting_row(key, label, value, enabled=None):
                if enabled is True:
                    value_style = "bold #1ed760"
                elif enabled is False:
                    value_style = "dim"
                else:
                    value_style = "#38bdf8"
                t.add_row(
                    Text(f"[{key}]", style="bold #7dd3fc"),
                    label,
                    Text(str(value), style=value_style),
                )

            setting_row("i", "Check interval", f"{profile.check_interval}h")
            setting_row("d", "Days to check", days)
            setting_row("s", "Sort by date", "On" if profile.sort_by_date else "Off", profile.sort_by_date)
            setting_row("r", "Skip remixes", "On" if profile.skip_remixes else "Off", profile.skip_remixes)
            setting_row("p", "Skip low popularity", "On" if profile.skip_low_popularity else "Off", profile.skip_low_popularity)
            setting_row("m", "Minimum popularity", profile.min_popularity)
            setting_row("a", "Skip long albums", "On" if profile.skip_long_albums else "Off", profile.skip_long_albums)
            setting_row("l", "Limit per album", "On" if profile.limit_songs_per_album else "Off", profile.limit_songs_per_album)
            setting_row("x", "Maximum songs per album", profile.max_songs_per_album)
            skip_similar = getattr(profile, "skip_similar_duplicates", False)
            setting_row("u", "Skip similar tracks", "On" if skip_similar else "Off", skip_similar)

            footer = Text("Tracked  ", style="dim")
            footer.append(f"{release_count} releases  ·  {track_count} tracks", style="white")
            footer.append("     [reset] Clear history", style="dim")
            body = Group(t, Rule(style="dim", characters="-"), footer)
            panel_width = min(68, max(54, console.size.width))
            console.print(Panel(
                body,
                title=Text(f"Settings  ·  {profile.name}", style="bold white"),
                width=panel_width,
                box=box.ROUNDED,
                border_style="#5f87d7",
                padding=(1, 2),
            ))
        else:
            print(f"\n  Settings — {profile.name}")
            print(f"  {'─'*48}")
            print(f"  [i] Check Interval : {profile.check_interval}h   "
                  f"[d] Days to Check : {days}")
            print(f"  [s] Sort by Date   : {'Yes' if profile.sort_by_date else 'No'}   "
                  f"[r] Skip Remixes  : {'Yes' if profile.skip_remixes else 'No'}")
            print(f"  [p] Low Popularity : {'Yes' if profile.skip_low_popularity else 'No'}   "
                  f"[m] Min Popularity: {profile.min_popularity}")
            print(f"  [a] Skip Long Albums: {'Yes' if profile.skip_long_albums else 'No'}   "
                  f"[l] Limit/Album   : {'Yes' if profile.limit_songs_per_album else 'No'}")
            print(f"  [x] Max Songs/Album: {profile.max_songs_per_album}   "
                  f"[u] Skip Similar  : {'Yes' if getattr(profile, 'skip_similar_duplicates', False) else 'No'}")
            print(f"\n  Tracked: {release_count} releases, {track_count} tracks — "
                  f"type 'reset' to clear")
            print()

    def _toggle(self, attr: str, label: str):
        profile = self._profile
        current = getattr(profile, attr, False)
        setattr(profile, attr, not current)
        self.config_manager.save()
        val = "Yes" if not current else "No"
        print_success(f"{label}: {val}")

    def _change_int_setting(self, attr: str, label: str, lo: int, hi: int):
        profile = self._profile
        current = getattr(profile, attr, 0)
        try:
            if RICH_AVAILABLE:
                from rich.prompt import IntPrompt
                val = IntPrompt.ask(label, default=current)
            else:
                val = int(input(f"{label} [{current}]: ") or str(current))
            if lo <= val <= hi:
                setattr(profile, attr, val)
                self.config_manager.save()
                print_success(f"{label}: {val}")
            else:
                print_error(f"Value must be {lo}–{hi}")
        except ValueError:
            print_error("Invalid number")

    def _reset_tracking(self):
        profile = self._profile
        release_count = len(profile.tracked_releases)
        track_count = len(getattr(profile, "tracked_tracks", {}))
        if release_count == 0 and track_count == 0:
            print_info("Nothing to reset")
            return
        print_warning(f"Will reset {release_count} releases and {track_count} tracks. "
                      f"All songs will be re-scanned on next check!")
        if _confirm("Reset all tracking?"):
            self.profile_manager.reset_tracked_releases(profile, mode="all")

    # ── import / export ──────────────────────────────────

    def _menu_import_export(self):
        app = self._app
        if not app:
            return

        items = [
            ("1", "Export current profile", "complete backup"),
            ("2", "Export all profiles", "complete backup"),
            ("3", "Export artists only", "current profile"),
            None,
            ("4", "Import file", "JSON backup"),
            ("5", "Preview file", "no changes made"),
        ]
        _print_menu("Import / Export", items)
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
