"""
Main entry point, CLI argument parsing, and daemon mode.
"""

import argparse
import signal
import sys
import os
import threading
import time
from typing import Optional, List

from .constants import (
    APP_NAME, APP_VERSION, RICH_AVAILABLE, CONFIG_DIR, CONFIG_FILE, TOKEN_CACHE,
    print_success, print_error, print_warning, print_info,
    parse_spotify_uri,
)


def create_argument_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="auto-new-releases",
        description=f"{APP_NAME} - Automatically track new releases from your favorite artists",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                      Start interactive mode
  %(prog)s check                Check current profile for new releases
  %(prog)s check --all          Check all profiles
  %(prog)s check --profile "My Profile"  Check specific profile
  %(prog)s daemon               Run in background, checking on schedule
  %(prog)s artists list         List tracked artists
  %(prog)s artists add "Artist Name"    Search and add artist
  %(prog)s export               Export current profile
  %(prog)s import backup.json   Import profiles from file
        """
    )

    parser.add_argument("--version", "-v", action="version", version=f"%(prog)s {APP_VERSION}")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress non-essential output")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # check
    check_parser = subparsers.add_parser("check", help="Check for new releases")
    check_parser.add_argument("--all", "-a", action="store_true", help="Check all profiles")
    check_parser.add_argument("--profile", "-p", type=str, help="Profile name to check")
    check_parser.add_argument("--dry-run", action="store_true", help="Show what would be added without adding")

    # daemon
    daemon_parser = subparsers.add_parser("daemon", help="Run in daemon mode")
    daemon_parser.add_argument("--interval", "-i", type=int, default=0, help="Override check interval (minutes, 0 = use profile settings)")
    daemon_parser.add_argument("--once", action="store_true", help="Check once and exit (for cron jobs)")

    # artists
    artists_parser = subparsers.add_parser("artists", help="Manage artists")
    artists_sub = artists_parser.add_subparsers(dest="artists_command")
    artists_sub.add_parser("list", help="List tracked artists")
    artists_add = artists_sub.add_parser("add", help="Add an artist")
    artists_add.add_argument("query", type=str, help="Artist name or Spotify URI")
    artists_remove = artists_sub.add_parser("remove", help="Remove an artist")
    artists_remove.add_argument("query", type=str, help="Artist name or number")
    artists_sub.add_parser("refresh", help="Refresh artist data")

    # playlist
    playlist_parser = subparsers.add_parser("playlist", help="Manage playlist")
    playlist_sub = playlist_parser.add_subparsers(dest="playlist_command")
    playlist_sub.add_parser("info", help="Show playlist info")
    playlist_set = playlist_sub.add_parser("set", help="Set target playlist")
    playlist_set.add_argument("uri", type=str, help="Playlist URI or URL")
    playlist_sub.add_parser("sort", help="Sort playlist by release date")
    playlist_sub.add_parser("dedupe", help="Remove duplicate tracks")
    playlist_sub.add_parser("analyze", help="Analyze playlist")

    # profile
    profile_parser = subparsers.add_parser("profile", help="Manage profiles")
    profile_sub = profile_parser.add_subparsers(dest="profile_command")
    profile_sub.add_parser("list", help="List all profiles")
    profile_sub.add_parser("show", help="Show current profile details")
    profile_switch = profile_sub.add_parser("switch", help="Switch to a profile")
    profile_switch.add_argument("name", type=str, help="Profile name")
    profile_create = profile_sub.add_parser("create", help="Create new profile")
    profile_create.add_argument("name", type=str, help="Profile name")
    profile_delete = profile_sub.add_parser("delete", help="Delete a profile")
    profile_delete.add_argument("name", type=str, help="Profile name")

    # export
    export_parser = subparsers.add_parser("export", help="Export profiles")
    export_parser.add_argument("--all", "-a", action="store_true", help="Export all profiles")
    export_parser.add_argument("--output", "-o", type=str, help="Output file path")
    export_parser.add_argument("--no-history", action="store_true", help="Exclude tracked releases history")

    # import
    import_parser = subparsers.add_parser("import", help="Import profiles")
    import_parser.add_argument("file", type=str, help="File to import")
    import_parser.add_argument("--mode", "-m", choices=["skip", "replace", "rename"], default="skip", help="How to handle duplicates")

    subparsers.add_parser("status", help="Show status and schedule")

    # config
    config_parser = subparsers.add_parser("config", help="Show/edit configuration")
    config_parser.add_argument("--show", action="store_true", help="Show current configuration")
    config_parser.add_argument("--reset-credentials", action="store_true", help="Reset Spotify credentials")

    return parser


class CLIHandler:
    """Handles CLI commands."""

    def __init__(self, app, quiet: bool = False):
        self.app = app
        self.quiet = quiet

    def log(self, message: str, level: str = "info"):
        if self.quiet and level == "info":
            return
        if level == "success":
            print_success(message)
        elif level == "error":
            print_error(message)
        elif level == "warning":
            print_warning(message)
        else:
            print_info(message)

    def cmd_check(self, args) -> int:
        if args.all:
            return self._check_all()
        elif args.profile:
            return self._check_profile(args.profile, args.dry_run)
        else:
            return self._check_current(args.dry_run)

    def _check_current(self, dry_run: bool = False) -> int:
        from .checker import CheckStatus

        profile = self.app.config_manager.get_active_profile()
        self.log(f"Checking profile: {profile.name}")

        if not profile.playlist_uri:
            self.log("No playlist configured!", "error")
            return 1
        if not profile.artists:
            self.log("No artists tracked!", "error")
            return 1
        if dry_run:
            self.log("Dry run - would check for new releases")
            return 0

        result = self.app.release_checker.check_profile(profile, silent=self.quiet)

        if result.status in (CheckStatus.SUCCESS, CheckStatus.NO_NEW):
            self.log(result.summary(), "success" if result.total_tracks_added > 0 else "info")
            return 0
        else:
            self.log(f"Check failed: {result.error_message}", "error")
            return 1

    def _check_profile(self, profile_name: str, dry_run: bool = False) -> int:
        profile = None
        for p in self.app.config_manager.config.profiles:
            if p.name.lower() == profile_name.lower():
                profile = p
                break

        if not profile:
            self.log(f"Profile not found: {profile_name}", "error")
            return 1

        original_id = self.app.config_manager.config.active_profile_id
        self.app.config_manager.config.active_profile_id = profile.id
        try:
            return self._check_current(dry_run)
        finally:
            self.app.config_manager.config.active_profile_id = original_id

    def _check_all(self) -> int:
        from .checker import CheckStatus

        profiles = self.app.config_manager.config.profiles
        self.log(f"Checking {len(profiles)} profiles...")

        total_added = 0
        failed = 0

        for profile in profiles:
            self.log(f"\nProfile: {profile.name}")
            if not profile.playlist_uri or not profile.artists:
                self.log("  Skipping - not configured", "warning")
                continue

            result = self.app.release_checker.check_profile(profile, silent=True)
            if result.status in (CheckStatus.SUCCESS, CheckStatus.NO_NEW):
                if result.total_tracks_added > 0:
                    self.log(f"  Added {result.total_tracks_added} tracks", "success")
                    total_added += result.total_tracks_added
                else:
                    self.log("  No new releases")
            else:
                self.log(f"  Failed: {result.error_message}", "error")
                failed += 1

        print()
        self.log(f"Total: {total_added} tracks added", "success")
        if failed > 0:
            self.log(f"{failed} profiles failed", "warning")
            return 1
        return 0

    def cmd_artists(self, args) -> int:
        if args.artists_command == "list":
            self.app.profile_manager.display_artists()
            return 0
        elif args.artists_command == "add":
            return self._artists_add(args.query)
        elif args.artists_command == "remove":
            return self._artists_remove(args.query)
        elif args.artists_command == "refresh":
            return self._artists_refresh()
        else:
            self.log("Use: artists list|add|remove|refresh")
            return 0

    def _artists_add(self, query: str) -> int:
        profile = self.app.config_manager.get_active_profile()
        artist_id = parse_spotify_uri(query, "artist")

        if artist_id:
            artist_data = self.app.spotify_api.get_artist(f"spotify:artist:{artist_id}")
            if artist_data:
                artist = self.app.spotify_api.artist_to_model(artist_data)
                return 0 if self.app.profile_manager.add_artist_to_profile(profile, artist) else 1
            self.log("Artist not found", "error")
            return 1
        else:
            results = self.app.spotify_api.search_artists(query)
            if not results:
                self.log("No artists found", "error")
                return 1
            artist = self.app.spotify_api.artist_to_model(results[0])
            self.log(f"Found: {artist.name}")
            return 0 if self.app.profile_manager.add_artist_to_profile(profile, artist) else 1

    def _artists_remove(self, query: str) -> int:
        profile = self.app.config_manager.get_active_profile()
        if not profile.artists:
            self.log("No artists to remove", "warning")
            return 1

        try:
            idx = int(query)
            artist = self.app.profile_manager.find_artist_by_index(profile, idx)
        except ValueError:
            artist = self.app.profile_manager.find_artist_by_name(profile, query)

        if artist:
            return 0 if self.app.profile_manager.remove_artist_from_profile(profile, artist.uri) else 1
        self.log("Artist not found", "error")
        return 1

    def _artists_refresh(self) -> int:
        profile = self.app.config_manager.get_active_profile()
        if not profile.artists:
            self.log("No artists to refresh", "warning")
            return 0

        updated = 0
        for i, artist in enumerate(profile.artists):
            if not self.quiet:
                print(f"  [{i+1}/{len(profile.artists)}] {artist.name}")
            data = self.app.spotify_api.get_artist(artist.uri)
            if data:
                profile.artists[i] = self.app.spotify_api.artist_to_model(data)
                updated += 1

        self.app.config_manager.save()
        self.log(f"Updated {updated} artists", "success")
        return 0

    def cmd_playlist(self, args) -> int:
        if args.playlist_command == "info":
            profile = self.app.config_manager.get_active_profile()
            if not profile.playlist_uri:
                self.log("No playlist configured", "warning")
                return 1
            self.app.playlist_ops.display_playlist_stats(profile.playlist_uri)
            return 0
        elif args.playlist_command == "set":
            profile = self.app.config_manager.get_active_profile()
            playlist_id = parse_spotify_uri(args.uri, "playlist")
            if not playlist_id:
                self.log("Invalid playlist URI/URL", "error")
                return 1
            playlist = self.app.playlist_ops.get_playlist_details(f"spotify:playlist:{playlist_id}")
            if playlist:
                profile.playlist_uri = playlist.get('uri', '')
                profile.playlist_name = playlist.get('name', '')
                self.app.config_manager.save()
                self.log(f"Set playlist to: {profile.playlist_name}", "success")
                return 0
            self.log("Playlist not found", "error")
            return 1
        elif args.playlist_command == "sort":
            profile = self.app.config_manager.get_active_profile()
            if not profile.playlist_uri:
                self.log("No playlist configured", "error")
                return 1
            result = self.app.playlist_tools.sorter.sort_by_release_date(profile.playlist_uri)
            if result.success:
                self.log(f"Sorted {result.tracks_sorted} tracks", "success")
                return 0
            self.log(f"Sort failed: {result.error_message}", "error")
            return 1
        elif args.playlist_command == "dedupe":
            profile = self.app.config_manager.get_active_profile()
            if not profile.playlist_uri:
                self.log("No playlist configured", "error")
                return 1
            result = self.app.playlist_tools.deduplicate(profile.playlist_uri)
            if result.success:
                if result.duplicates_removed > 0:
                    self.log(f"Removed {result.duplicates_removed} duplicates", "success")
                else:
                    self.log("No duplicates found")
                return 0
            self.log(f"Dedupe failed: {result.error_message}", "error")
            return 1
        elif args.playlist_command == "analyze":
            profile = self.app.config_manager.get_active_profile()
            if not profile.playlist_uri:
                self.log("No playlist configured", "error")
                return 1
            self.app.playlist_tools.analyzer.display_analysis(profile.playlist_uri, detailed=True)
            return 0
        else:
            self.log("Use: playlist info|set|sort|dedupe|analyze")
            return 0

    def cmd_profile(self, args) -> int:
        if args.profile_command == "list":
            self.app.profile_manager.display_profiles()
            return 0
        elif args.profile_command == "show":
            self.app.profile_manager.display_profile_details()
            return 0
        elif args.profile_command == "switch":
            for p in self.app.config_manager.config.profiles:
                if p.name.lower() == args.name.lower():
                    return 0 if self.app.profile_manager.switch_profile(p.id) else 1
            self.log(f"Profile not found: {args.name}", "error")
            return 1
        elif args.profile_command == "create":
            self.app.profile_manager.create_profile(args.name)
            return 0
        elif args.profile_command == "delete":
            for p in self.app.config_manager.config.profiles:
                if p.name.lower() == args.name.lower():
                    return 0 if self.app.profile_manager.delete_profile(p.id) else 1
            self.log(f"Profile not found: {args.name}", "error")
            return 1
        else:
            self.log("Use: profile list|show|switch|create|delete")
            return 0

    def cmd_export(self, args) -> int:
        from .importer import ImportExportManager
        include_tracked = not args.no_history

        if args.all:
            result = self.app.import_export_manager.export_all(args.output, include_tracked)
        else:
            result = self.app.import_export_manager.export_current_profile(args.output, include_tracked)

        if result.success:
            self.log(f"Exported to: {result.file_path}", "success")
            self.log(f"  Profiles: {result.profiles_exported}")
            self.log(f"  Artists: {result.artists_exported}")
            return 0
        self.log(f"Export failed: {result.error_message}", "error")
        return 1

    def cmd_import(self, args) -> int:
        from .importer import ImportMode

        mode_map = {"skip": ImportMode.SKIP, "replace": ImportMode.REPLACE, "rename": ImportMode.RENAME}
        mode = mode_map.get(args.mode, ImportMode.SKIP)
        result = self.app.import_export_manager.import_file(args.file, mode)

        if result.success:
            self.log("Import complete!", "success")
            if result.profiles_imported:
                self.log(f"  Imported: {result.profiles_imported}")
            if result.profiles_replaced:
                self.log(f"  Replaced: {result.profiles_replaced}")
            if result.profiles_skipped:
                self.log(f"  Skipped: {result.profiles_skipped}")
            return 0
        self.log(f"Import failed: {result.error_message}", "error")
        return 1

    def cmd_status(self) -> int:
        if self.app.scheduled_checker:
            self.app.scheduled_checker.display_schedule()
        return 0

    def cmd_config(self, args) -> int:
        if args.reset_credentials:
            self.app.config_manager.config.spotify_client_id = ""
            self.app.config_manager.config.spotify_client_secret = ""
            self.app.config_manager.save()
            if TOKEN_CACHE.exists():
                TOKEN_CACHE.unlink()
            self.log("Credentials reset. Re-run to set new credentials.", "success")
            return 0

        profile = self.app.config_manager.get_active_profile()
        print(f"\nConfiguration Directory: {CONFIG_DIR}")
        print(f"Config File: {CONFIG_FILE}")
        print(f"Profiles: {len(self.app.config_manager.config.profiles)}")
        print(f"Active Profile: {profile.name}")
        print(f"Credentials Set: {'Yes' if self.app.config_manager.config.spotify_client_id else 'No'}")
        print()
        return 0


class Daemon:
    """Background daemon for scheduled checking."""

    def __init__(self, app, interval_override: int = 0):
        self.app = app
        self.interval_override = interval_override
        self.running = False
        self._stop_event = threading.Event()

    def start(self):
        self.running = True
        self._stop_event.clear()

        print_info(f"{APP_NAME} daemon started")
        print_info("Press Ctrl+C to stop")
        print()

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        self._run_loop()

    def stop(self):
        self.running = False
        self._stop_event.set()
        print_info("Daemon stopping...")

    def _handle_signal(self, signum, frame):
        self.stop()

    def _run_loop(self):
        while self.running:
            if self.app.scheduled_checker:
                due_profiles = self.app.scheduled_checker.get_profiles_due()

                if due_profiles:
                    print_info(f"Checking {len(due_profiles)} due profiles...")
                    results = self.app.scheduled_checker.check_due_profiles(silent=False)

                    for result in results:
                        if result.total_tracks_added > 0:
                            print_success(f"[{result.profile_name}] Added {result.total_tracks_added} tracks")

            sleep_seconds = self.interval_override * 60 if self.interval_override > 0 else 300
            next_check = time.time() + sleep_seconds
            print_info(f"Next check in {sleep_seconds // 60} minutes...")

            while self.running and time.time() < next_check:
                self._stop_event.wait(timeout=30)
                if self._stop_event.is_set():
                    break

        print_info("Daemon stopped.")
