"""
Auto New Releases - Spotify new release tracker package.

Main Application class and package-level entry point.
"""

import sys
import os
import socket
from typing import Optional

# Package version
__version__ = "2.0.0"
__author__ = "Adrian"

# Public API
from .constants import (
    APP_NAME,
    APP_VERSION,
    CONFIG_DIR,
    CONFIG_FILE,
    TOKEN_CACHE,
    RICH_AVAILABLE,
    print_success,
    print_error,
    print_warning,
    print_info,
    parse_spotify_uri,
    clear_screen,
)
from .models import Artist, Profile, Config
from .config import ConfigManager
from .auth import SpotifyAuthManager
from .api import SpotifyAPI, ReleaseFetcher, ArtistSearcher
from .bridge_server import BridgeServer, BRIDGE_PORT
from .bridge_api import BridgeAPI
from .profile import ProfileManager, ProfileMenu
from .playlist import PlaylistOperations, PlaylistSelector, PlaylistRestorer
from .filters import DuplicateDetector, RemixDetector, ReleaseDateFilter
from .checker import ReleaseChecker, InteractiveChecker, ScheduledChecker, CheckStatus, ProfileCheckResult
from .tools import PlaylistTools, SortCriteria, SortOrder, SortResult, DedupeResult
from .importer import ImportExportManager, ImportExportMenu, ImportMode
from .ui import ApplicationUI, display_header, display_status_bar
from .cli import create_argument_parser, CLIHandler, Daemon

__all__ = [
    "Application",
    "APP_NAME",
    "APP_VERSION",
    "ConfigManager",
    "ProfileManager",
    "SpotifyAPI",
    "BridgeAPI",
    "BridgeServer",
    "ReleaseChecker",
    "InteractiveChecker",
    "ScheduledChecker",
    "PlaylistTools",
    "ImportExportManager",
    "ImportMode",
    "CheckStatus",
]


class Application:
    """
    Central application class that wires all components together.

    Usage:
        app = Application()
        app.initialize()
        exit_code = app.run()
    """

    def __init__(self):
        # Core managers
        self.config_manager: Optional[ConfigManager] = None
        self.profile_manager: Optional[ProfileManager] = None

        # Authentication and API
        self.auth_manager: Optional[SpotifyAuthManager] = None
        self.spotify_api = None  # SpotifyAPI or BridgeAPI
        self.bridge_server: Optional[BridgeServer] = None
        self._bridge_mode: bool = False

        # Playlist operations
        self.playlist_ops: Optional[PlaylistOperations] = None
        self.playlist_selector: Optional[PlaylistSelector] = None
        self.playlist_restorer: Optional[PlaylistRestorer] = None

        # Checkers
        self.release_checker: Optional[ReleaseChecker] = None
        self.interactive_checker: Optional[InteractiveChecker] = None
        self.scheduled_checker: Optional[ScheduledChecker] = None

        # Tools
        self.playlist_tools: Optional[PlaylistTools] = None
        self.artist_searcher: Optional[ArtistSearcher] = None

        # Import/Export
        self.import_export_manager: Optional[ImportExportManager] = None
        self.import_export_menu: Optional[ImportExportMenu] = None

        # UI
        self.app_ui: Optional[ApplicationUI] = None

    @staticmethod
    def _bridge_server_reachable() -> bool:
        """Quick TCP probe — returns True if something is listening on the bridge port."""
        try:
            with socket.create_connection(("127.0.0.1", BRIDGE_PORT), timeout=0.5):
                return True
        except OSError:
            return False

    def initialize(self) -> bool:
        """
        Initialize all components.

        Tries to use the Spicetify bridge first (no OAuth needed).
        Falls back to standard Spotify OAuth if bridge is not available.

        Returns:
            True if initialization was successful and API is authorized.
        """
        # 1. Load config (ConfigManager loads on __init__)
        self.config_manager = ConfigManager()

        # 2. Profile manager
        self.profile_manager = ProfileManager(self.config_manager)

        # 3. Try bridge mode first ----------------------------------------
        #    Start BridgeServer, then wait up to 3 s for the extension to poll.
        self.bridge_server = BridgeServer()
        self.bridge_server.start()

        import time
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if self.bridge_server.extension_connected:
                break
            time.sleep(0.2)

        if self.bridge_server.extension_connected:
            self._bridge_mode = True
            self.spotify_api = BridgeAPI(self.bridge_server)
            print_success("\U0001f3b8 Spicetify bridge active — no OAuth needed")
        else:
            # Bridge not available — fall back to OAuth
            self.bridge_server.stop()
            self.bridge_server = None

            # 4. Auth manager
            self.auth_manager = SpotifyAuthManager(self.config_manager)

            if not self.config_manager.config.spotify_client_id:
                print_info("Spotify credentials not configured.")
                self.auth_manager.setup_credentials()

            if not self.auth_manager.ensure_authenticated():
                return False

            # 5. Build Spotify API
            self.spotify_api = SpotifyAPI(self.auth_manager)

        # 6. Playlist operations
        self.playlist_ops = PlaylistOperations(self.spotify_api)
        self.playlist_selector = PlaylistSelector(self.spotify_api, self.playlist_ops)
        self.playlist_restorer = PlaylistRestorer(self.playlist_ops)

        # 7. Checkers
        self.release_checker = ReleaseChecker(self.spotify_api, self.playlist_ops, self.config_manager)
        self.interactive_checker = InteractiveChecker(self.spotify_api, self.playlist_ops, self.config_manager)
        self.scheduled_checker = ScheduledChecker(self.spotify_api, self.playlist_ops, self.config_manager)

        # 8. Tools
        self.playlist_tools = PlaylistTools(self.spotify_api, self.playlist_ops)
        self.artist_searcher = ArtistSearcher(self.spotify_api)

        # 9. Import/Export
        self.import_export_manager = ImportExportManager(self.config_manager)
        self.import_export_menu = ImportExportMenu(self.import_export_manager)

        # 10. UI
        self.app_ui = ApplicationUI(self.config_manager, self.profile_manager)
        self.app_ui.set_app(self)

        return True

    def _stop_bridge(self):
        """Stop the bridge server if running (safe to call multiple times)."""
        if self.bridge_server:
            self.bridge_server.stop()
            self.bridge_server = None

    def run(self, args=None) -> int:
        """
        Run the application.

        Args:
            args: Parsed argparse.Namespace, or None to run interactive mode.

        Returns:
            Exit code (0 = success, non-zero = error).
        """
        try:
            if args is None or args.command is None:
                # Interactive mode
                if not self.initialize():
                    print_error("Failed to initialize. Check your Spotify credentials.")
                    return 1
                self.app_ui.run()
                return 0

            # CLI mode
            quiet = getattr(args, 'quiet', False)

            if not self.initialize():
                print_error("Failed to initialize.")
                return 1

            handler = CLIHandler(self, quiet=quiet)

            command = args.command

            if command == "check":
                return handler.cmd_check(args)
            elif command == "daemon":
                if args.once:
                    return handler._check_all()
                else:
                    daemon = Daemon(self, interval_override=args.interval)
                    daemon.start()
                    return 0
            elif command == "artists":
                return handler.cmd_artists(args)
            elif command == "playlist":
                return handler.cmd_playlist(args)
            elif command == "profile":
                return handler.cmd_profile(args)
            elif command == "export":
                return handler.cmd_export(args)
            elif command == "import":
                return handler.cmd_import(args)
            elif command == "status":
                return handler.cmd_status()
            elif command == "config":
                return handler.cmd_config(args)
            else:
                print_error(f"Unknown command: {command}")
                return 1
        finally:
            self._stop_bridge()


def main():
    """Main entry point for the package."""
    parser = create_argument_parser()
    args = parser.parse_args()

    app = Application()
    sys.exit(app.run(args))
