"""
ANR Bridge API
~~~~~~~~~~~~~~
Drop-in replacement for SpotifyAPI that routes all calls through
the local BridgeServer ↔ Spicetify extension instead of hitting
the (now-blocked) Spotify Web API directly.

The BridgeAPI surface mirrors SpotifyAPI exactly so that all
existing callers (checker.py, playlist.py, tools.py …) work
without modification.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .api import SpotifyAPIError
from .bridge_server import BridgeServer
from .constants import parse_spotify_uri, print_warning, print_info
from .models import Artist


class BridgeAPI:
    """
    Spotify API replacement backed by the in-app Spicetify bridge.

    Parameters
    ----------
    server:
        A *running* BridgeServer instance.
    default_timeout:
        Seconds to wait for the Spicetify extension to handle each request.
    """

    def __init__(self, server: BridgeServer, default_timeout: float = 300.0):
        self._server = server
        self._timeout = default_timeout

    # Internal dispatch
    def _call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Send a request through the bridge and return the result."""
        try:
            return self._server.call(method, params, timeout=self._timeout)
        except TimeoutError as e:
            raise SpotifyAPIError(str(e)) from e
        except RuntimeError as e:
            raise SpotifyAPIError(str(e)) from e

    # Status helpers (used by Application.initialize)
    def is_available(self) -> bool:
        """Return True if the Spicetify extension is actively polling."""
        return self._server.extension_connected

    # =========================================================================
    # USER
    # =========================================================================
    def get_current_user(self) -> Dict:
        return self._call("get_current_user")

    def get_current_user_id(self) -> str:
        return self.get_current_user()["id"]

    # =========================================================================
    # ARTIST
    # =========================================================================
    def search_artists(self, query: str, limit: int = 10) -> List[Dict]:
        if not query or len(query.strip()) < 2:
            return []
        return self._call("search_artists", {"query": query, "limit": limit}) or []

    def get_artist(self, artist_uri: str) -> Optional[Dict]:
        artist_id = parse_spotify_uri(artist_uri, "artist")
        if not artist_id:
            return None
        return self._call("get_artist", {"artist_id": artist_id})

    def get_multiple_artists(self, artist_ids: List[str]) -> List[Dict]:
        if not artist_ids:
            return []
        result = self._call("get_multiple_artists", {"artist_ids": artist_ids})
        return [a for a in (result or []) if a]

    def get_artist_albums(
        self,
        artist_uri: str,
        include_groups: str = "album,single",
        limit: int = 50,
    ) -> List[Dict]:
        artist_id = parse_spotify_uri(artist_uri, "artist")
        if not artist_id:
            return []
        return self._call(
            "get_artist_albums",
            {"artist_id": artist_id, "include_groups": include_groups, "limit": limit},
        ) or []

    def get_artist_top_tracks(self, artist_uri: str, country: str = "US") -> List[Dict]:
        artist_id = parse_spotify_uri(artist_uri, "artist")
        if not artist_id:
            return []
        return self._call(
            "get_artist_top_tracks", {"artist_id": artist_id, "country": country}
        ) or []

    def artist_to_model(self, artist_data: Dict) -> Artist:
        """Convert raw Spotify artist dict to local Artist model (no bridge call)."""
        return Artist(
            uri=artist_data.get("uri", ""),
            name=artist_data.get("name", "Unknown"),
            image=(
                artist_data.get("images", [{}])[0].get("url")
                if artist_data.get("images")
                else None
            ),
            followers=artist_data.get("followers", {}).get("total"),
            last_follower_update=time.time(),
        )

    # =========================================================================
    # ALBUM
    # =========================================================================
    def get_album(self, album_uri: str) -> Optional[Dict]:
        album_id = parse_spotify_uri(album_uri, "album")
        if not album_id:
            return None
        return self._call("get_album", {"album_id": album_id})

    def get_album_tracks(self, album_uri: str) -> List[Dict]:
        album_id = parse_spotify_uri(album_uri, "album")
        if not album_id:
            return []
        return self._call("get_album_tracks", {"album_id": album_id}) or []

    def get_multiple_albums(self, album_uris: List[str]) -> List[Dict]:
        """Fetch multiple albums via small batched bridge calls.

        Python drives the loop so each individual bridge call stays short
        (keeping the JS poll loop alive). Backs off on 429 responses.
        """
        if not album_uris:
            return []

        album_ids = [parse_spotify_uri(u, "album") for u in album_uris]
        album_ids = [aid for aid in album_ids if aid]
        if not album_ids:
            return []

        BATCH_SIZE = 10
        BACKOFF_BASE = 3.0
        MAX_BACKOFF = 30.0
        backoff = BACKOFF_BASE

        all_results: List[Dict] = []

        for i in range(0, len(album_ids), BATCH_SIZE):
            batch = album_ids[i: i + BATCH_SIZE]

            while True:
                try:
                    resp = self._call("get_albums_batch", {"album_ids": batch})
                    results = (resp or {}).get("results", [])
                    had_429 = (resp or {}).get("had_429", False)
                    all_results.extend(r for r in results if r)

                    if had_429:
                        print_warning(f"[BridgeAPI] 429 detected in album batch, waiting {backoff:.0f}s...")
                        time.sleep(backoff)
                        backoff = min(backoff * 2, MAX_BACKOFF)
                    else:
                        backoff = max(backoff / 2, BACKOFF_BASE)

                    if (i // BATCH_SIZE) % 10 == 0:
                        print_info(f"  Albums fetched: {len(all_results)}/{len(album_ids)}")

                    break

                except Exception as e:
                    msg = str(e)
                    if "429" in msg or "too many" in msg.lower():
                        print_warning(f"[BridgeAPI] 429 on batch, waiting {backoff:.0f}s...")
                        time.sleep(backoff)
                        backoff = min(backoff * 2, MAX_BACKOFF)
                    else:
                        print_warning(f"[BridgeAPI] Album batch error: {e}")
                        break

        return all_results

    def get_album_release_dates(
        self, album_uris: List[str], playlist_uri: Optional[str] = None
    ) -> Dict[str, Optional[str]]:
        """Fetch release dates for many albums, using a local cache to avoid rate limits."""
        if not album_uris:
            return {}

        import json
        from .constants import CONFIG_DIR

        album_ids = [parse_spotify_uri(u, "album") for u in album_uris]
        album_ids = [aid for aid in album_ids if aid]
        if not album_ids:
            return {}

        cache_file = CONFIG_DIR / "album_dates_cache.json"
        local_cache: Dict[str, Optional[str]] = {}

        try:
            if cache_file.exists():
                with open(cache_file, "r", encoding="utf-8") as f:
                    local_cache = json.load(f)
        except Exception as e:
            print_warning(f"[BridgeAPI] Failed to load album dates cache: {e}")

        needed_ids = [aid for aid in album_ids if aid not in local_cache]

        if needed_ids:
            print_info(f"Fetching release dates for {len(needed_ids)} new albums (already have {len(album_ids) - len(needed_ids)} cached)...")
            params: Dict[str, Any] = {"album_ids": needed_ids}
            if playlist_uri:
                pid = parse_spotify_uri(playlist_uri, "playlist")
                if pid:
                    params["playlist_id"] = pid

            result = self._call("get_album_release_dates", params)

            # Update cache
            for aid, date in (result or {}).items():
                local_cache[aid] = date

            try:
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(local_cache, f)
            except Exception as e:
                print_warning(f"[BridgeAPI] Failed to save album dates cache: {e}")
        else:
            print_info(f"Using cached release dates for all {len(album_ids)} albums.")

        date_map: Dict[str, Optional[str]] = {}
        for aid in album_ids:
            date_map[f"spotify:album:{aid}"] = local_cache.get(aid)

        return date_map

    # =========================================================================
    # TRACK
    # =========================================================================
    def get_track(self, track_uri: str) -> Optional[Dict]:
        track_id = parse_spotify_uri(track_uri, "track")
        if not track_id:
            return None
        return self._call("get_track", {"track_id": track_id})

    def get_multiple_tracks(self, track_uris: List[str]) -> List[Dict]:
        if not track_uris:
            return []
        track_ids = [parse_spotify_uri(u, "track") for u in track_uris]
        track_ids = [tid for tid in track_ids if tid]
        if not track_ids:
            return []
        result = self._call("get_multiple_tracks", {"track_ids": track_ids})
        return [t for t in (result or []) if t]

    def get_tracks_audio_features(self, track_uris: List[str]) -> List[Dict]:
        # Audio features endpoint is not available via bridge (not critical).
        # Return empty list; callers should handle this gracefully.
        return []

    # =========================================================================
    # PLAYLIST — read
    # =========================================================================
    def get_user_playlists(self, limit: int = 50) -> List[Dict]:
        return self._call("get_user_playlists", {"limit": limit}) or []

    def get_playlist(self, playlist_uri: str, skip_cache: bool = False) -> Optional[Dict]:
        playlist_id = parse_spotify_uri(playlist_uri, "playlist")
        if not playlist_id:
            return None
        return self._call("get_playlist", {"playlist_id": playlist_id})

    # =========================================================================
    # PLAYLIST — write
    # =========================================================================
    def create_playlist(
        self,
        name: str,
        description: str = "",
        public: bool = False,
    ) -> Optional[Dict]:
        return self._call(
            "create_playlist",
            {"name": name, "description": description, "public": public},
        )

    def remove_playlist_tracks(self, playlist_id: str, track_uris: List[str]) -> bool:
        """Remove tracks from a playlist. Returns True on success."""
        if not track_uris:
            return True
        uris: List[str] = []
        for u in track_uris:
            if isinstance(u, dict):
                uris.append(u.get("uri", ""))
            elif isinstance(u, str):
                uris.append(u)
        uris = [u for u in uris if u]
        if not uris:
            return True
        try:
            self._call(
                "remove_tracks_from_playlist",
                {"playlist_id": playlist_id, "track_uris": uris},
            )
            return True
        except SpotifyAPIError:
            return False

    def add_tracks_to_playlist(self, playlist_id: str, track_uris: List[str]) -> bool:
        """Add tracks to a playlist. Returns True on success."""
        if not track_uris:
            return True
        try:
            self._call(
                "add_tracks_to_playlist",
                {"playlist_id": playlist_id, "track_uris": track_uris},
            )
            return True
        except SpotifyAPIError:
            return False

    def replace_playlist_tracks(self, playlist_id: str, track_uris: List[str]) -> bool:
        """Replace all tracks in a playlist. Returns True on success."""
        if not track_uris:
            return True
        try:
            self._call(
                "replace_playlist_tracks",
                {"playlist_id": playlist_id, "track_uris": track_uris},
            )
            return True
        except SpotifyAPIError:
            return False

    # =========================================================================
    # Cache / compatibility stubs
    # =========================================================================
    def clear_cache(self, key: Optional[str] = None):
        pass  # no-op — bridge has no local cache

    def _rate_limit(self):
        """No-op: bridge has no local rate limiting (handled by the extension)."""
        pass

    def _handle_api_error(self, e: Exception, context: str = "API call"):
        """Re-raise as SpotifyAPIError so callers get a consistent exception type."""
        raise SpotifyAPIError(f"{context}: {e}") from e