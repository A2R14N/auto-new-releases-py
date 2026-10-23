"""
Profile management: ProfileManager and ProfileMenu.
"""

from datetime import datetime
from typing import Optional, List, Tuple, Any

from .constants import (
    RICH_AVAILABLE, console,
    print_success, print_error, print_warning, print_info,
    generate_id, format_number,
)
from .output import open_block, data_table
from .models import Artist, Profile, Config
from .config import ConfigManager


class ProfileManager:
    """Manages user profiles - create, delete, rename, switch, list."""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager

    @property
    def config(self) -> Config:
        return self.config_manager.config

    @property
    def profiles(self) -> List[Profile]:
        return self.config.profiles

    @property
    def active_profile(self) -> Profile:
        return self.config_manager.get_active_profile()

    def save(self):
        """Save current configuration."""
        self.config_manager.save()

    # PROFILE CRUD OPERATIONS
    def create_profile(self, name: str) -> Profile:
        """Create a new profile."""
        profile_id = generate_id()
        new_profile = Profile(
            id=profile_id,
            name=name.strip()
        )

        self.config.profiles.append(new_profile)
        self.config.active_profile_id = profile_id
        self.save()

        print_success(f"Created profile: {name}")
        return new_profile

    def delete_profile(self, profile_id: str) -> bool:
        """Delete a profile by ID."""
        if len(self.profiles) <= 1:
            print_error("Cannot delete the last profile!")
            return False

        profile = self.config_manager.get_profile_by_id(profile_id)
        if not profile:
            print_error("Profile not found!")
            return False

        profile_name = profile.name
        self.config.profiles = [p for p in self.profiles if p.id != profile_id]

        if self.config.active_profile_id == profile_id:
            self.config.active_profile_id = self.profiles[0].id

        self.save()
        print_success(f"Deleted profile: {profile_name}")
        return True

    def rename_profile(self, profile_id: str, new_name: str) -> bool:
        """Rename a profile."""
        profile = self.config_manager.get_profile_by_id(profile_id)
        if not profile:
            print_error("Profile not found!")
            return False

        old_name = profile.name
        profile.name = new_name.strip()
        self.save()

        print_success(f"Renamed '{old_name}' to '{new_name}'")
        return True

    def switch_profile(self, profile_id: str) -> bool:
        """Switch to a different profile."""
        profile = self.config_manager.get_profile_by_id(profile_id)
        if not profile:
            print_error("Profile not found!")
            return False

        if self.config.active_profile_id == profile_id:
            print_info(f"Already on profile: {profile.name}")
            return True

        self.config.active_profile_id = profile_id
        self.save()

        print_success(f"Switched to profile: {profile.name}")
        return True

    def set_profile_enabled(self, profile_id: str, enabled: bool) -> bool:
        """Include or exclude a profile from bulk and scheduled release checks."""
        profile = self.config_manager.get_profile_by_id(profile_id)
        if not profile:
            print_error('Profile not found!')
            return False
        if profile.enabled == enabled:
            print_info(f"'{profile.name}' is already {'enabled' if enabled else 'disabled'}")
            return True
        previous = profile.enabled
        profile.enabled = enabled
        try:
            self.save()
        except Exception as error:
            profile.enabled = previous
            print_error(f'Could not save profile status: {error}')
            return False
        print_success(f"{'Enabled' if enabled else 'Disabled'} '{profile.name}' for all-profile and scheduled checks")
        return True

    def duplicate_profile(self, profile_id: str, new_name: str) -> Optional[Profile]:
        """Duplicate an existing profile."""
        source_profile = self.config_manager.get_profile_by_id(profile_id)
        if not source_profile:
            print_error("Source profile not found!")
            return None

        new_profile_id = generate_id()

        new_profile = Profile(
            id=new_profile_id,
            name=new_name.strip(),
            artists=[Artist.from_dict(a.to_dict()) for a in source_profile.artists],
            playlist_uri=source_profile.playlist_uri,
            playlist_name=source_profile.playlist_name,
            check_interval=source_profile.check_interval,
            last_check=None,
            tracked_releases={},
            days_to_check=source_profile.days_to_check,
            sort_by_date=source_profile.sort_by_date,
            skip_remixes=source_profile.skip_remixes,
            skip_low_popularity=source_profile.skip_low_popularity,
            min_popularity=source_profile.min_popularity,
            skip_long_albums=source_profile.skip_long_albums,
            max_songs=source_profile.max_songs,
            limit_songs_per_album=source_profile.limit_songs_per_album,
            max_songs_per_album=source_profile.max_songs_per_album,
            skip_similar_duplicates=source_profile.skip_similar_duplicates,
            enabled=source_profile.enabled,
        )

        self.config.profiles.append(new_profile)
        self.config.active_profile_id = new_profile_id
        self.save()

        print_success(f"Duplicated '{source_profile.name}' as '{new_name}'")
        return new_profile

    # PROFILE LISTING & DISPLAY
    def list_profiles(self) -> List[Tuple[str, str, int, str, bool]]:
        """Get list of all profiles with their info."""
        result = []
        for profile in self.profiles:
            result.append((
                profile.id,
                profile.name,
                len(profile.artists),
                profile.playlist_name or "Not set",
                profile.id == self.config.active_profile_id
            ))
        return result

    def display_profiles(self):
        """Display all profiles in a formatted table."""
        profiles = self.list_profiles()

        if RICH_AVAILABLE:
            table = data_table(title="Profiles", show_header=True, header_style='anr.heading')
            table.add_column("#", style='anr.shortcut', width=3)
            table.add_column("Name", style='anr.text')
            table.add_column("Artists", justify="right")
            table.add_column("Playlist", style='anr.secondary')
            table.add_column("Status", justify="center")

            for idx, (pid, name, artists, playlist, is_active) in enumerate(profiles, 1):
                profile = self.config_manager.get_profile_by_id(pid)
                status = ('Active · ' if is_active else '') + ('Enabled' if profile.enabled else 'Disabled')
                table.add_row(
                    str(idx),
                    name,
                    str(artists),
                    playlist[:30] + "..." if len(playlist) > 30 else playlist,
                    status
                )

            console.print(table)
        else:
            print("\n=== Profiles ===")
            for idx, (pid, name, artists, playlist, is_active) in enumerate(profiles, 1):
                profile = self.config_manager.get_profile_by_id(pid)
                status = ('[ACTIVE] ' if is_active else '') + ('[Enabled]' if profile.enabled else '[Disabled]')
                print(f"  {idx}. {name} - {artists} artists - {playlist} {status}")
            print()

    def display_profile_details(self, profile: Optional[Profile] = None):
        """Display detailed information about a profile."""
        if profile is None:
            profile = self.active_profile

        is_active = profile.id == self.config.active_profile_id
        last_check = "Never" if not profile.last_check else \
            datetime.fromtimestamp(profile.last_check).strftime("%Y-%m-%d %H:%M")

        if RICH_AVAILABLE:
            details = [
                f"[bold]Name:[/] {profile.name}",
                f"[bold]Status:[/] {'[anr.accent]●[/] Active' if is_active else '[anr.secondary]Inactive[/]'}",
                f"[bold]Bulk & scheduled checks:[/] {'Enabled' if profile.enabled else 'Disabled'}",
                f"[bold]Artists:[/] {len(profile.artists)}",
                f"[bold]Playlist:[/] {profile.playlist_name or '[anr.error]Not configured[/]'}",
                f"[bold]Last Check:[/] {last_check}",
                f"[bold]Tracked Releases:[/] {len(profile.tracked_releases)}",
                f"[bold]Tracked Tracks:[/] {len(profile.tracked_tracks)}",
                "",
                "[anr.heading]Settings:[/]",
                f"  Check Interval: {profile.check_interval}h",
                f"  Days to Check: {profile.days_to_check if profile.days_to_check > 0 else 'All time'}",
                f"  Sort by Date: {'Yes' if profile.sort_by_date else 'No'}",
                f"  Skip Remixes: {'Yes' if profile.skip_remixes else 'No'}",
                f"  Skip Low Popularity: {'Yes' if profile.skip_low_popularity else 'No'}"
                    + (f" (<{profile.min_popularity})" if profile.skip_low_popularity else ""),
                f"  Skip Long Albums: {'Yes' if profile.skip_long_albums else 'No'}"
                    + (f" (>{profile.max_songs} tracks)" if profile.skip_long_albums else ""),
                f"  Limit per Album: {'Yes' if profile.limit_songs_per_album else 'No'}"
                    + (f" ({profile.max_songs_per_album} max)" if profile.limit_songs_per_album else ""),
            ]

            panel = open_block(f"[bold]Profile: {profile.name}[/]", "\n".join(details))
            console.print(panel)
        else:
            print(f"\n=== Profile: {profile.name} ===")
            print(f"  Status: {'Active' if is_active else 'Inactive'}")
            print(f"  Bulk & scheduled checks: {'Enabled' if profile.enabled else 'Disabled'}")
            print(f"  Artists: {len(profile.artists)}")
            print(f"  Playlist: {profile.playlist_name or 'Not configured'}")
            print(f"  Last Check: {last_check}")
            print(f"  Tracked Releases: {len(profile.tracked_releases)}")
            print("\n  Settings:")
            print(f"    Check Interval: {profile.check_interval}h")
            print(f"    Days to Check: {profile.days_to_check if profile.days_to_check > 0 else 'All time'}")
            print(f"    Sort by Date: {'Yes' if profile.sort_by_date else 'No'}")
            print(f"    Skip Remixes: {'Yes' if profile.skip_remixes else 'No'}")
            print(f"    Skip Low Popularity: {'Yes' if profile.skip_low_popularity else 'No'}")
            print(f"    Skip Long Albums: {'Yes' if profile.skip_long_albums else 'No'}")
            print(f"    Limit per Album: {'Yes' if profile.limit_songs_per_album else 'No'}")
            print()

    # PROFILE SETTINGS
    def update_profile_setting(self, profile: Profile, setting: str, value: Any) -> bool:
        """Update a profile setting."""
        valid_settings = {
            'check_interval': int,
            'days_to_check': int,
            'sort_by_date': bool,
            'skip_remixes': bool,
            'skip_low_popularity': bool,
            'min_popularity': int,
            'skip_long_albums': bool,
            'max_songs': int,
            'limit_songs_per_album': bool,
            'max_songs_per_album': int,
            'playlist_uri': str,
            'playlist_name': str,
        }

        if setting not in valid_settings:
            print_error(f"Unknown setting: {setting}")
            return False

        expected_type = valid_settings[setting]
        try:
            if expected_type == bool and isinstance(value, str):
                value = value.lower() in ('true', 'yes', '1', 'on')
            else:
                value = expected_type(value)

            setattr(profile, setting, value)
            self.save()
            print_success(f"Updated {setting} = {value}")
            return True
        except (ValueError, TypeError) as e:
            print_error(f"Invalid value for {setting}: {e}")
            return False

    def reset_tracked_releases(self, profile: Optional[Profile] = None, mode: str = "all") -> bool:
        """Reset tracked data for a profile. mode: 'all' | 'tracks' | 'releases'"""
        if profile is None:
            profile = self.active_profile

        release_count = len(profile.tracked_releases)
        track_count = len(getattr(profile, 'tracked_tracks', {}))

        if mode in ('all', 'tracks', 'releases'):
            profile.pending_artists = {}
            profile.last_scan = None

        if mode == "all":
            profile.tracked_releases = {}
            profile.tracked_tracks = {}
            self.save()
            print_success(f"Cleared {release_count} releases and {track_count} tracks from '{profile.name}'")
        elif mode == "tracks":
            profile.tracked_tracks = {}
            self.save()
            print_success(f"Cleared {track_count} tracked tracks from '{profile.name}'")
            print_info("Previously filtered songs can now be re-added")
        elif mode == "releases":
            profile.tracked_releases = {}
            self.save()
            print_success(f"Cleared {release_count} tracked releases from '{profile.name}'")
            print_info("Albums will be re-scanned, but already-tracked songs won't be re-added")

        return True

    # ARTIST MANAGEMENT IN PROFILE
    def add_artist_to_profile(self, profile: Profile, artist: Artist) -> bool:
        """Add an artist to a profile."""
        for existing in profile.artists:
            if existing.uri == artist.uri:
                print_warning(f"Artist '{artist.name}' is already tracked")
                return False

        profile.artists.append(artist)
        profile.last_scan = None
        self.save()
        print_success(f"Added '{artist.name}' to tracking")
        return True

    def remove_artist_from_profile(self, profile: Profile, artist_uri: str) -> bool:
        """Remove an artist from a profile."""
        return self.remove_artists_from_profile(profile, [artist_uri]) == 1

    def remove_artists_from_profile(self, profile: Profile, artist_uris: List[str]) -> int:
        """Remove a validated set of artists and their pending recovery in one save."""
        selected = set(artist_uris)
        if not selected:
            return 0
        if not selected.issubset({artist.uri for artist in profile.artists}):
            print_error('Artist not found in profile')
            return 0
        removed = [artist for artist in profile.artists if artist.uri in selected]
        profile.artists = [artist for artist in profile.artists if artist.uri not in selected]
        for uri in selected:
            profile.pending_artists.pop(uri, None)
        self.save()
        if len(removed) == 1:
            print_success(f"Removed '{removed[0].name}' from tracking")
        else:
            print_success(f'Removed {len(removed)} artists from tracking')
        return len(removed)

    def clear_all_artists(self, profile: Profile) -> int:
        """Remove all artists from a profile."""
        count = len(profile.artists)
        profile.artists = []
        self.save()
        print_success(f"Removed {count} artists from '{profile.name}'")
        return count

    def list_artists(self, profile: Optional[Profile] = None) -> List[Artist]:
        """Get list of artists in a profile."""
        if profile is None:
            profile = self.active_profile
        return profile.artists

    def display_artists(self, profile: Optional[Profile] = None):
        """Display artists in a profile as a formatted table."""
        if profile is None:
            profile = self.active_profile

        artists = profile.artists

        if not artists:
            print_info(f"No artists tracked in '{profile.name}'")
            return

        if RICH_AVAILABLE:
            table = data_table(
                title=f"Tracked Artists ({len(artists)})",
                show_header=True,
                header_style='anr.heading',
            )
            table.add_column("#", style='anr.shortcut', width=4)
            table.add_column("Name", style='anr.text')
            table.add_column("Followers", justify="right")
            table.add_column("URI", style='anr.secondary')

            for idx, artist in enumerate(artists, 1):
                followers = format_number(artist.followers) if artist.followers else "Unknown"
                table.add_row(
                    str(idx),
                    artist.name,
                    followers,
                    artist.uri.split(":")[-1][:12] + "..."
                )

            console.print(table)
        else:
            print(f"\n=== Tracked Artists ({len(artists)}) ===")
            for idx, artist in enumerate(artists, 1):
                followers = format_number(artist.followers) if artist.followers else "Unknown"
                print(f"  {idx}. {artist.name} ({followers} followers)")
            print()

    def find_artist_by_index(self, profile: Profile, index: int) -> Optional[Artist]:
        """Find an artist by their display index (1-based)."""
        if 1 <= index <= len(profile.artists):
            return profile.artists[index - 1]
        return None

    def find_artist_by_name(self, profile: Profile, name: str) -> Optional[Artist]:
        """Find an artist by name (case-insensitive partial match)."""
        name_lower = name.lower()
        for artist in profile.artists:
            if name_lower in artist.name.lower():
                return artist
        return None

# INTERACTIVE PROFILE MENU
class ProfileMenu:
    """Interactive menu for profile management."""

    def __init__(self, profile_manager: ProfileManager):
        self.pm = profile_manager

    def prompt(self, message: str, default: str = "") -> str:
        """Prompt user for input."""
        if RICH_AVAILABLE:
            from rich.prompt import Prompt
            return Prompt.ask(message, default=default, console=console) if default else Prompt.ask(message, console=console)
        else:
            prompt_text = f"{message} [{default}]: " if default else f"{message}: "
            result = input(prompt_text).strip()
            return result if result else default

    def confirm(self, message: str, default: bool = False) -> bool:
        """Prompt user for confirmation."""
        if RICH_AVAILABLE:
            from rich.prompt import Confirm
            return Confirm.ask(message, default=default, console=console)
        else:
            suffix = " [Y/n]: " if default else " [y/N]: "
            result = input(message + suffix).strip().lower()
            if not result:
                return default
            return result in ('y', 'yes')

    def prompt_int(self, message: str, default: int = 0, min_val: int = None, max_val: int = None) -> int:
        """Prompt user for integer input."""
        while True:
            if RICH_AVAILABLE:
                from rich.prompt import IntPrompt
                try:
                    value = IntPrompt.ask(message, default=default, console=console)
                except KeyboardInterrupt:
                    return default
            else:
                try:
                    prompt_text = f"{message} [{default}]: "
                    result = input(prompt_text).strip()
                    value = int(result) if result else default
                except ValueError:
                    print_error("Please enter a valid number")
                    continue

            if min_val is not None and value < min_val:
                print_error(f"Value must be at least {min_val}")
                continue
            if max_val is not None and value > max_val:
                print_error(f"Value must be at most {max_val}")
                continue

            return value

    def select_profile(self, prompt_message: str = "Select profile") -> Optional[Profile]:
        """Let user select a profile from a numbered list."""
        profiles = self.pm.list_profiles()

        if not profiles:
            print_error("No profiles available")
            return None

        self.pm.display_profiles()

        try:
            choice = self.prompt_int(
                f"{prompt_message} (1-{len(profiles)}, 0 to cancel)",
                default=0,
                min_val=0,
                max_val=len(profiles)
            )

            if choice == 0:
                return None

            profile_id = profiles[choice - 1][0]
            return self.pm.config_manager.get_profile_by_id(profile_id)

        except (ValueError, IndexError):
            print_error("Invalid selection")
            return None

    def run_create_profile(self):
        """Interactive profile creation."""
        name = self.prompt("Enter profile name")
        if not name:
            print_warning("Cancelled")
            return
        self.pm.create_profile(name)

    def run_delete_profile(self):
        """Interactive profile deletion."""
        profile = self.select_profile("Select profile to delete")
        if not profile:
            return
        if not self.confirm(f"Are you sure you want to delete '{profile.name}'?"):
            print_warning("Cancelled")
            return
        self.pm.delete_profile(profile.id)

    def run_rename_profile(self):
        """Interactive profile renaming."""
        profile = self.select_profile("Select profile to rename")
        if not profile:
            return
        new_name = self.prompt("Enter new name", default=profile.name)
        if not new_name or new_name == profile.name:
            print_warning("Cancelled")
            return
        self.pm.rename_profile(profile.id, new_name)

    def run_switch_profile(self):
        """Interactive profile switching."""
        profile = self.select_profile("Select profile to switch to")
        if not profile:
            return
        self.pm.switch_profile(profile.id)

    def run_duplicate_profile(self):
        """Interactive profile duplication."""
        profile = self.select_profile("Select profile to duplicate")
        if not profile:
            return
        new_name = self.prompt("Enter name for the copy", default=f"{profile.name} (Copy)")
        if not new_name:
            print_warning("Cancelled")
            return
        self.pm.duplicate_profile(profile.id, new_name)

    def run_edit_settings(self):
        """Use the same Direct settings editor for every interactive entry point."""
        from .ui import ApplicationUI
        ApplicationUI(self.pm.config_manager, self.pm)._menu_settings()

    def run_reset_tracked(self):
        """Interactive reset of tracked releases."""
        profile = self.pm.active_profile
        count = len(profile.tracked_releases)

        if count == 0:
            print_info("No tracked releases to reset")
            return

        if self.confirm(f"Reset {count} tracked releases for '{profile.name}'?"):
            self.pm.reset_tracked_releases(profile)
