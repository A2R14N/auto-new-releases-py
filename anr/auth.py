"""
Spotify OAuth authentication.
"""

from typing import Optional, Tuple, Dict

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException

from .constants import (
    SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI,
    SPOTIFY_SCOPES, TOKEN_CACHE,
    RICH_AVAILABLE, console,
    print_info, print_success, print_error,
)
from .config import ConfigManager


class SpotifyAuthManager:
    """Handles Spotify authentication."""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.sp: Optional[spotipy.Spotify] = None
        self._current_user: Optional[Dict] = None

    def get_credentials(self) -> Tuple[str, str]:
        """Get Spotify API credentials, prompting if needed."""
        client_id = self.config_manager.config.spotify_client_id or SPOTIFY_CLIENT_ID
        client_secret = self.config_manager.config.spotify_client_secret or SPOTIFY_CLIENT_SECRET

        if not client_id or not client_secret:
            print_info("Spotify API credentials not found.")
            print("\nTo use this application, you need Spotify API credentials.")
            print("1. Go to https://developer.spotify.com/dashboard")
            print("2. Create a new application")
            print("3. Add 'http://127.0.0.1:8888/callback' as a Redirect URI")
            print("4. Copy the Client ID and Client Secret\n")

            if RICH_AVAILABLE:
                from rich.prompt import Prompt
                client_id = Prompt.ask("Enter your Spotify Client ID")
                client_secret = Prompt.ask("Enter your Spotify Client Secret")
            else:
                client_id = input("Enter your Spotify Client ID: ").strip()
                client_secret = input("Enter your Spotify Client Secret: ").strip()

            # Save credentials
            self.config_manager.config.spotify_client_id = client_id
            self.config_manager.config.spotify_client_secret = client_secret
            self.config_manager.save()

            print_success("Credentials saved!")

        return client_id, client_secret

    def authenticate(self) -> bool:
        """Authenticate with Spotify and create client."""
        try:
            client_id, client_secret = self.get_credentials()

            auth_manager = SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=SPOTIFY_REDIRECT_URI,
                scope=" ".join(SPOTIFY_SCOPES),
                cache_path=str(TOKEN_CACHE),
                open_browser=True
            )

            self.sp = spotipy.Spotify(auth_manager=auth_manager)

            # Test the connection
            self._current_user = self.sp.current_user()
            print_success(f"Authenticated as: {self._current_user['display_name']}")
            return True

        except SpotifyException as e:
            print_error(f"Spotify authentication failed: {e}")
            return False
        except Exception as e:
            print_error(f"Authentication error: {e}")
            return False

    def get_client(self) -> Optional[spotipy.Spotify]:
        """Get the authenticated Spotify client."""
        if not self.sp:
            self.authenticate()
        return self.sp

    def get_current_user(self) -> Optional[Dict]:
        """Get current user info (cached)."""
        if not self._current_user and self.sp:
            self._current_user = self.sp.current_user()
        return self._current_user

    def setup_credentials(self):
        """Prompt the user for Spotify credentials and save them."""
        self.get_credentials()

    def ensure_authenticated(self) -> bool:
        """Ensure the client is authenticated, triggering OAuth if needed."""
        if self.sp:
            return True
        return self.authenticate()
