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
        from rich.panel import Panel
        lines = []
        for item in items:
            if item is None:
                lines.append("[dim]" + "─" * 42 + "[/]")
            else:
                key, label, hint = item
                if key:
                    line = f"  [bold cyan]{key:>2}[/]  [bold white]{label}[/]"
                    if hint:
                        line += f"  [dim]{hint}[/]"
                    lines.append(line)
        if show_back:
            lines.append("")
            lines.append("   [bold cyan]0[/]  [bold white]Back[/]")
        console.print(Panel("\n".join(lines), title=f"[bold]{title}[/]",
                            border_style="cyan", padding=(1, 2)))
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
        display_header()
        display_status_bar(self.config_manager)

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
        _print_menu("Main Menu", items, show_back=False)
        choice = _choice()
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
            _print_menu(f"Artists  ({len(profile.artists)} tracked)", items)
            choice = _choice()

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
            choice = _choice()

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
            # Always show list at top
            self.profile_manager.display_profiles()

            items = [
                ("1", "Switch Profile", ""),
                ("2", "Create New Profile", ""),
                None,
                ("3", "Rename Profile", ""),
                ("4", "Duplicate Profile", ""),
                ("5", "Delete Profile", "disabled" if len(profiles) <= 1 else ""),
            ]
            _print_menu("Profiles", items)
            choice = _choice()

            if choice == "0":
                break
            elif choice == "1":
                self.profile_menu.run_switch_profile()
            elif choice == "2":
                self.profile_menu.run_create_profile()
            elif choice == "3":
                self.profile_menu.run_rename_profile()
            elif choice == "4":
                self.profile_menu.run_duplicate_profile()
            elif choice == "5" and len(profiles) > 1:
                self.profile_menu.run_delete_profile()
            elif choice == "5":
                print_warning("Cannot delete the only profile")

    # ── settings ─────────────────────────────────────────

    def _menu_settings(self):
        """Compact inline settings toggle — no numbered items, just type a letter."""
        while True:
            profile = self._profile
            self._display_settings_table(profile)

            print("  Type a letter to change, or Enter/0 to go back:")
            raw = _choice("")

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
            from rich.table import Table
            t = Table(show_header=False, box=None, padding=(0, 3))
            t.add_column(style="bold cyan", width=4)
            t.add_column(style="bold white", width=24)
            t.add_column(style="white", width=10)
            t.add_column(style="bold cyan", width=4)
            t.add_column(style="bold white", width=24)
            t.add_column(style="white")

            def g(v): return f"[green]{v}[/]" if v else f"[dim]{v}[/]"

            t.add_row("\\[i]", "Check Interval", f"{profile.check_interval}h",
                      "\\[d]", "Days to Check", str(days))
            t.add_row("\\[s]", "Sort by Date", g("Yes" if profile.sort_by_date else "No"),
                      "\\[r]", "Skip Remixes", g("Yes" if profile.skip_remixes else "No"))
            t.add_row("\\[p]", "Skip Low Popularity", g("Yes" if profile.skip_low_popularity else "No"),
                      "\\[m]", "Min Popularity", str(profile.min_popularity))
            t.add_row("\\[a]", "Skip Long Albums", g("Yes" if profile.skip_long_albums else "No"),
                      "\\[l]", "Limit per Album", g("Yes" if profile.limit_songs_per_album else "No"))
            t.add_row("\\[x]", "Max Songs/Album", str(profile.max_songs_per_album),
                      "\\[u]", "Skip Similar", g("Yes" if getattr(profile, "skip_similar_duplicates", False) else "No"))

            from rich.panel import Panel
            console.print(Panel(t, title=f"[bold]Settings — {profile.name}[/]",
                                border_style="cyan", padding=(1, 2)))
            console.print(f"  [dim]Tracked: {release_count} releases, {track_count} tracks "
                          f"— type [bold]reset[/bold] to clear[/]")
            console.print()
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

        print("\n  Export:  [1] Current profile  [2] All profiles  [3] Artists only")
        print("  Import:  [4] Import file      [5] Preview file")
        print("  [0] Back\n")
        choice = _choice()

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
