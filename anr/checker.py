"""
Core logic for checking new releases from tracked artists,
processing them, and adding to playlists.
"""

import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Set, Callable

from .constants import (
    RICH_AVAILABLE, console,
    print_success, print_error, print_warning, print_info,
)
from .models import Artist, Profile
from .api import SpotifyAPI, ReleaseFetcher
from .config import ConfigManager
from .playlist import PlaylistOperations
from .filters import RemixDetector, ReleaseDateFilter


def _print_section_header(title: str, position: str = "") -> None:
    """Print a quiet, easy-to-scan section header."""
    width = 64
    if RICH_AVAILABLE:
        heading = f"[bold cyan]{title}[/]"
        if position:
            heading += f" [dim]{position:>{max(1, width - len(title) - len(position))}}[/]"
        console.print(heading)
        console.print("-" * width, style="dim")
    else:
        suffix = f"  {position}" if position else ""
        print(f"{title}{suffix}")
        print("-" * width)


def _aurora_color(position: float) -> str:
    """Return the shared aurora color for a normalized 0..1 position."""
    stops = ((95, 135, 215), (56, 189, 248), (30, 215, 96))
    position = max(0.0, min(1.0, position))
    if position <= 0.5:
        start, end, amount = stops[0], stops[1], position * 2
    else:
        start, end, amount = stops[1], stops[2], (position - 0.5) * 2

    rgb = tuple(round(a + (b - a) * amount) for a, b in zip(start, end))
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _print_artist_progress(current: int, total: int, artist_name: str) -> None:
    """Print artist progress using an aurora blue-to-green gradient."""
    counter = f"{current:>{len(str(total))}}/{total}"
    if RICH_AVAILABLE:
        from rich.text import Text

        progress = (current - 1) / max(total - 1, 1)
        color = _aurora_color(progress)
        line = Text("  ")
        line.append(counter, style=f"bold {color}")
        line.append("  ")
        line.append(artist_name, style=color)
        console.print(line)
    else:
        print(f"  {counter}  {artist_name}")


class CheckStatus(Enum):
    """Status of a release check operation."""
    SUCCESS = "success"
    PARTIAL = "partial"
    NO_NEW = "no_new"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class ArtistCheckResult:
    """Result of checking a single artist."""
    artist: Artist
    status: CheckStatus
    releases_found: int = 0
    releases_processed: int = 0
    tracks_added: int = 0
    tracks_filtered: int = 0
    error_message: str = ""
    new_releases: List[Dict] = field(default_factory=list)


@dataclass
class ProfileCheckResult:
    """Result of checking an entire profile."""
    profile_name: str
    status: CheckStatus
    total_artists: int = 0
    artists_checked: int = 0
    artists_with_new: int = 0
    total_releases: int = 0
    total_tracks_added: int = 0
    total_tracks_filtered: int = 0
    artist_results: List[ArtistCheckResult] = field(default_factory=list)
    filter_stats: Optional[object] = None
    error_message: str = ""
    duration_seconds: float = 0

    def summary(self) -> str:
        """Get summary of check results."""
        if self.status == CheckStatus.ERROR:
            return f"Error: {self.error_message}"
        if self.total_tracks_added == 0:
            return f"No new releases found for {self.artists_checked} artists"
        parts = [f"Added {self.total_tracks_added} tracks", f"from {self.artists_with_new} artists"]
        if self.total_tracks_filtered > 0:
            parts.append(f"({self.total_tracks_filtered} filtered)")
        return " ".join(parts)

    def display(self):
        """Display detailed results."""
        if RICH_AVAILABLE:
            from rich.panel import Panel
            from rich.table import Table

            status_colors = {
                CheckStatus.SUCCESS: "green",
                CheckStatus.PARTIAL: "yellow",
                CheckStatus.NO_NEW: "blue",
                CheckStatus.ERROR: "red",
                CheckStatus.SKIPPED: "dim",
            }
            color = status_colors.get(self.status, "white")

            lines = [
                f"[bold]Status:[/] [{color}]{self.status.value.upper()}[/]",
                f"[bold]Duration:[/] {self.duration_seconds:.1f}s",
                "",
                f"[bold]Artists Checked:[/] {self.artists_checked}/{self.total_artists}",
                f"[bold]Artists with New Releases:[/] {self.artists_with_new}",
                f"[bold]Total Releases Found:[/] {self.total_releases}",
                "",
                f"[bold green]Tracks Added:[/] {self.total_tracks_added}",
                f"[bold yellow]Tracks Filtered:[/] {self.total_tracks_filtered}",
            ]

            if self.error_message:
                lines.append(f"\n[bold red]Error:[/] {self.error_message}")

            panel = console.print(Panel(
                "\n".join(lines),
                title=f"[bold]Check Results: {self.profile_name}[/]",
                border_style=color
            ))

            if self.artist_results and any(r.tracks_added > 0 for r in self.artist_results):
                table = Table(title="Artist Breakdown", show_header=True, header_style="bold cyan")
                table.add_column("Artist", style="white")
                table.add_column("Releases", justify="right")
                table.add_column("Added", justify="right", style="green")
                table.add_column("Filtered", justify="right", style="yellow")

                for result in self.artist_results:
                    if result.tracks_added > 0 or result.releases_found > 0:
                        table.add_row(
                            result.artist.name[:30],
                            str(result.releases_found),
                            str(result.tracks_added),
                            str(result.tracks_filtered)
                        )

                console.print(table)
        else:
            print(f"\n=== Check Results: {self.profile_name} ===")
            print(f"  Status: {self.status.value.upper()}")
            print(f"  Duration: {self.duration_seconds:.1f}s")
            print(f"  Artists Checked: {self.artists_checked}/{self.total_artists}")
            print(f"  Artists with New: {self.artists_with_new}")
            print(f"  Releases Found: {self.total_releases}")
            print(f"  Tracks Added: {self.total_tracks_added}")
            print(f"  Tracks Filtered: {self.total_tracks_filtered}")

            if self.error_message:
                print(f"  Error: {self.error_message}")

            if self.artist_results and any(r.tracks_added > 0 for r in self.artist_results):
                print("\n  Artist Breakdown:")
                for result in self.artist_results:
                    if result.tracks_added > 0:
                        print(f"    {result.artist.name}: +{result.tracks_added} tracks")
            print()


@dataclass
class CheckProgress:
    """Progress information for release checking."""
    phase: str  # 'init', 'artist', 'processing', 'adding', 'sorting', 'done'
    artist_current: int = 0
    artist_total: int = 0
    artist_name: str = ""
    release_current: int = 0
    release_total: int = 0
    tracks_found: int = 0
    message: str = ""


ProgressCallback = Callable[[CheckProgress], None]


class ReleaseChecker:
    """Main class for checking new releases from tracked artists."""

    def __init__(
        self,
        spotify_api: SpotifyAPI,
        playlist_ops: PlaylistOperations,
        config_manager: ConfigManager
    ):
        self.api = spotify_api
        self.playlist_ops = playlist_ops
        self.config_manager = config_manager
        self.release_fetcher = ReleaseFetcher(spotify_api)

    def check_profile(
        self,
        profile: Profile,
        progress_callback: Optional[ProgressCallback] = None,
        silent: bool = False
    ) -> ProfileCheckResult:
        """Check all artists in a profile for new releases."""
        # Import here to avoid circular
        from .tools import PlaylistTools

        start_time = time.time()

        result = ProfileCheckResult(
            profile_name=profile.name,
            status=CheckStatus.SUCCESS,
            total_artists=len(profile.artists)
        )

        if not profile.playlist_uri:
            result.status = CheckStatus.ERROR
            result.error_message = "No playlist configured"
            return result

        if not profile.artists:
            result.status = CheckStatus.ERROR
            result.error_message = "No artists to check"
            return result

        try:
            if progress_callback:
                progress_callback(CheckProgress(phase='init', message='Fetching existing playlist tracks...'))

            existing_tracks = self.playlist_ops.get_playlist_tracks(profile.playlist_uri)
            existing_uris = {t.uri for t in existing_tracks}

            existing_signatures = set()
            if getattr(profile, "skip_similar_duplicates", False):
                for t in existing_tracks:
                    existing_signatures.add(f"{t.name.lower()}|||{t.primary_artist.lower()}")

            if not silent:
                print_info(f"Playlist   {len(existing_uris):,} existing tracks")
                print_info(f"Artists    {len(profile.artists)} to check")
                print()

            if not hasattr(profile, 'tracked_tracks') or profile.tracked_tracks is None:
                profile.tracked_tracks = {}

            all_new_tracks: List[str] = []

            for idx, artist in enumerate(profile.artists):
                if progress_callback:
                    progress_callback(CheckProgress(
                        phase='artist',
                        artist_current=idx + 1,
                        artist_total=len(profile.artists),
                        artist_name=artist.name,
                        message=f"Checking: {artist.name}"
                    ))

                if not silent:
                    _print_artist_progress(idx + 1, len(profile.artists), artist.name)

                try:
                    all_releases = self.release_fetcher.get_artist_releases(artist.uri)
                except Exception as artist_err:
                    print_warning(f"    Skipped {artist.name} (bridge error): {type(artist_err).__name__}")
                    result.artist_results.append(ArtistCheckResult(
                        artist=artist,
                        status=CheckStatus.ERROR,
                        error_message=str(artist_err)
                    ))
                    result.artists_checked += 1
                    continue

                recent_releases = ReleaseDateFilter.filter_by_days(all_releases, profile.days_to_check)
                new_releases = [
                    r for r in recent_releases
                    if r.get('uri') not in profile.tracked_releases
                ]

                result.total_releases += len(new_releases)

                if not new_releases:
                    result.artists_checked += 1
                    continue

                result.artists_with_new += 1
                artist_tracks_added = 0
                artist_tracks_filtered = 0

                for release in new_releases:
                    release_uri = release.get('uri')
                    release_name = release.get('name', 'Unknown')

                    if not silent:
                        print_info(f"  Release: {release_name}")

                    album_details = self.api.get_album(release_uri)
                    if not album_details:
                        continue

                    album_tracks = album_details.get('tracks', {}).get('items', [])
                    album_popularity = album_details.get('popularity', 0)

                    if profile.skip_long_albums and len(album_tracks) > profile.max_songs:
                        if not silent:
                            print_warning(f"    Skipping album: too many tracks ({len(album_tracks)} > {profile.max_songs})")
                        result.total_tracks_filtered += len(album_tracks)
                        artist_tracks_filtered += len(album_tracks)
                        profile.tracked_releases[release_uri] = time.time()
                        continue

                    if profile.skip_low_popularity and album_popularity < profile.min_popularity:
                        if not silent:
                            print_warning(f"    Skipping album: low popularity ({album_popularity} < {profile.min_popularity})")
                        result.total_tracks_filtered += len(album_tracks)
                        artist_tracks_filtered += len(album_tracks)
                        profile.tracked_releases[release_uri] = time.time()
                        continue

                    tracks_to_add = []

                    for track in album_tracks:
                        track_uri = track.get('uri')
                        track_name = track.get('name', '')

                        if not track_uri:
                            continue

                        if track_uri in existing_uris:
                            if not silent:
                                print(f"    [in playlist] {track_name}")
                            artist_tracks_filtered += 1
                            result.total_tracks_filtered += 1
                            continue

                        if getattr(profile, 'skip_similar_duplicates', False):
                            track_artists = track.get('artists', [])
                            primary_artist = (track_artists[0].get('name', '') if track_artists else '').lower()
                            if f"{track_name.lower()}|||{primary_artist}" in existing_signatures:
                                if not silent:
                                    print(f"    [similar duplicate] {track_name}")
                                artist_tracks_filtered += 1
                                result.total_tracks_filtered += 1
                                continue

                        if track_uri in profile.tracked_tracks:
                            if not silent:
                                print(f"    [previously processed] {track_name}")
                            artist_tracks_filtered += 1
                            result.total_tracks_filtered += 1
                            continue

                        if profile.skip_remixes:
                            if RemixDetector.is_remix_or_variant(track_name, release_name):
                                if not silent:
                                    print(f"    [remix/variant] {track_name}")
                                profile.tracked_tracks[track_uri] = time.time()
                                artist_tracks_filtered += 1
                                result.total_tracks_filtered += 1
                                continue

                        tracks_to_add.append(track_uri)
                        if getattr(profile, 'skip_similar_duplicates', False):
                            track_artists = track.get('artists', [])
                            primary_artist = (track_artists[0].get('name', '') if track_artists else '').lower()
                            existing_signatures.add(f"{track_name.lower()}|||{primary_artist}")
                            
                        if not silent:
                            print_success(f"    [adding] {track_name}")

                    if profile.limit_songs_per_album and len(tracks_to_add) > profile.max_songs_per_album:
                        track_details = self.api.get_multiple_tracks(tracks_to_add)
                        if track_details:
                            sorted_tracks = sorted(
                                track_details,
                                key=lambda t: t.get('popularity', 0),
                                reverse=True
                            )
                            kept_uris = [
                                t.get('uri') for t in sorted_tracks[:profile.max_songs_per_album]
                                if t.get('uri')
                            ]
                            for t in sorted_tracks[profile.max_songs_per_album:]:
                                uri = t.get('uri')
                                if uri:
                                    profile.tracked_tracks[uri] = time.time()
                                    if not silent:
                                        print_warning(f"    [album limit] {t.get('name', 'Unknown')}")

                            filtered_count = len(tracks_to_add) - len(kept_uris)
                            tracks_to_add = kept_uris
                            artist_tracks_filtered += filtered_count
                            result.total_tracks_filtered += filtered_count

                    for track_uri in tracks_to_add:
                        profile.tracked_tracks[track_uri] = time.time()

                    all_new_tracks.extend(tracks_to_add)
                    artist_tracks_added += len(tracks_to_add)
                    existing_uris.update(tracks_to_add)
                    profile.tracked_releases[release_uri] = time.time()

                result.artist_results.append(ArtistCheckResult(
                    artist=artist,
                    status=CheckStatus.SUCCESS,
                    releases_found=len(new_releases),
                    tracks_added=artist_tracks_added,
                    tracks_filtered=artist_tracks_filtered
                ))

                result.artists_checked += 1

            result.total_tracks_added = len(all_new_tracks)

            if all_new_tracks:
                if progress_callback:
                    progress_callback(CheckProgress(
                        phase='adding',
                        tracks_found=len(all_new_tracks),
                        message=f'Adding {len(all_new_tracks)} tracks to playlist...'
                    ))

                if not silent:
                    print_info(f"Adding {len(all_new_tracks)} new tracks to playlist...")

                added, failed = self.playlist_ops.add_tracks(profile.playlist_uri, all_new_tracks)

                if failed > 0:
                    result.status = CheckStatus.PARTIAL
                    result.error_message = f"Failed to add {failed} tracks"
                    result.total_tracks_added = added

                if profile.sort_by_date and added > 0:
                    if progress_callback:
                        progress_callback(CheckProgress(phase='sorting', message='Sorting playlist by release date...'))

                    if not silent:
                        print_info("Sorting playlist by release date...")

                    time.sleep(2)

                    try:
                        from .tools import PlaylistTools
                        tools = PlaylistTools(self.api, self.playlist_ops)
                        tools.sorter.sort_by_release_date(profile.playlist_uri)
                    except Exception:
                        pass
            else:
                result.status = CheckStatus.NO_NEW

            profile.last_check = time.time()
            self.config_manager.save()

            result.duration_seconds = time.time() - start_time

            if progress_callback:
                progress_callback(CheckProgress(
                    phase='done',
                    tracks_found=result.total_tracks_added,
                    message='Check complete!'
                ))

            return result

        except Exception as e:
            self.config_manager.save()

            result.status = CheckStatus.ERROR
            result.error_message = str(e)
            result.duration_seconds = time.time() - start_time
            traceback.print_exc()
            return result

    def check_all_profiles(
        self,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
        silent: bool = False
    ) -> List[ProfileCheckResult]:
        """Check all profiles for new releases."""
        profiles = self.config_manager.config.profiles
        results = []

        for idx, profile in enumerate(profiles):
            if progress_callback:
                progress_callback(profile.name, idx + 1, len(profiles))

            if not silent:
                print()
                _print_section_header(profile.name, f"Profile {idx + 1} of {len(profiles)}")

            result = self.check_profile(profile, silent=silent)
            results.append(result)

            if not silent:
                result.display()

        return results


class InteractiveChecker:
    """Interactive release checker with terminal UI."""

    def __init__(
        self,
        spotify_api: SpotifyAPI,
        playlist_ops: PlaylistOperations,
        config_manager: ConfigManager
    ):
        self.checker = ReleaseChecker(spotify_api, playlist_ops, config_manager)
        self.config_manager = config_manager

    def run_check(self, profile: Optional[Profile] = None) -> ProfileCheckResult:
        """Run an interactive check with progress display."""
        if profile is None:
            profile = self.config_manager.get_active_profile()

        if not profile.playlist_uri:
            print_error("No playlist configured for this profile!")
            print_info("Use the playlist menu to set a target playlist first.")
            return ProfileCheckResult(
                profile_name=profile.name,
                status=CheckStatus.ERROR,
                error_message="No playlist configured"
            )

        if not profile.artists:
            print_error("No artists tracked in this profile!")
            print_info("Use the artists menu to add artists first.")
            return ProfileCheckResult(
                profile_name=profile.name,
                status=CheckStatus.ERROR,
                error_message="No artists"
            )

        print()
        print_info(f"Profile: {profile.name}")
        print_info(f"Artists: {len(profile.artists)}")
        print_info(f"Playlist: {profile.playlist_name}")
        print_info(f"Days to check: {profile.days_to_check if profile.days_to_check > 0 else 'All time'}")
        print()

        if RICH_AVAILABLE:
            from rich.prompt import Confirm
            if not Confirm.ask("Start check?", default=True):
                print_warning("Cancelled")
                return ProfileCheckResult(profile_name=profile.name, status=CheckStatus.SKIPPED)
        else:
            if input("Start check? [Y/n]: ").lower() == 'n':
                print_warning("Cancelled")
                return ProfileCheckResult(profile_name=profile.name, status=CheckStatus.SKIPPED)

        print()

        if RICH_AVAILABLE:
            from rich.progress import (
                Progress, ProgressColumn, SpinnerColumn, TextColumn,
                TaskProgressColumn,
            )
            from rich.text import Text

            class AuroraBarColumn(ProgressColumn):
                """A live progress bar using the shared ANR aurora palette."""

                def __init__(self, width: int = 24):
                    super().__init__()
                    self.width = width

                def render(self, task):
                    ratio = 0.0 if task.total is None else task.completed / max(task.total, 1)
                    ratio = max(0.0, min(1.0, ratio))
                    filled = round(self.width * ratio)
                    bar = Text()
                    for index in range(self.width):
                        if index < filled:
                            position = index / max(self.width - 1, 1)
                            bar.append("━", style=f"bold {_aurora_color(position)}")
                        else:
                            bar.append("━", style="bright_black")
                    return bar

            with Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]{task.description}"),
                AuroraBarColumn(),
                TaskProgressColumn(),
                TextColumn("[dim]{task.fields[status]}"),
                console=console
            ) as progress:
                task = progress.add_task(
                    "Checking releases...",
                    total=len(profile.artists),
                    status=""
                )

                def update_progress(p: CheckProgress):
                    if p.phase == 'artist':
                        progress.update(
                            task,
                            completed=p.artist_current,
                            description=f"Checking {p.artist_name}...",
                            status=f"{p.artist_current}/{p.artist_total}"
                        )
                    elif p.phase == 'adding':
                        progress.update(task, description="Adding tracks...", status=p.message)
                    elif p.phase == 'sorting':
                        progress.update(task, description="Sorting playlist...", status="")

                result = self.checker.check_profile(profile, update_progress, silent=True)
        else:
            def simple_progress(p: CheckProgress):
                if p.phase == 'artist':
                    print(f"  [{p.artist_current}/{p.artist_total}] {p.artist_name}")
                elif p.phase == 'adding':
                    print(f"  {p.message}")

            result = self.checker.check_profile(profile, simple_progress, silent=True)

        print()
        result.display()
        return result

    def run_check_all(self) -> List[ProfileCheckResult]:
        """Run check on all profiles interactively."""
        profiles = self.config_manager.config.profiles

        if not profiles:
            print_error("No profiles configured!")
            return []

        print_info(f"This will check {len(profiles)} profiles:")
        for p in profiles:
            print(f"  - {p.name}: {len(p.artists)} artists -> {p.playlist_name or 'No playlist'}")
        print()

        if RICH_AVAILABLE:
            from rich.prompt import Confirm
            if not Confirm.ask("Check all profiles?", default=True):
                print_warning("Cancelled")
                return []
        else:
            if input("Check all profiles? [Y/n]: ").lower() == 'n':
                print_warning("Cancelled")
                return []

        print()

        results = []
        total_added = 0

        for idx, profile in enumerate(profiles):
            print()
            _print_section_header(profile.name, f"Profile {idx + 1} of {len(profiles)}")

            if not profile.playlist_uri:
                print_warning("Skipping - no playlist configured")
                results.append(ProfileCheckResult(
                    profile_name=profile.name,
                    status=CheckStatus.SKIPPED,
                    error_message="No playlist"
                ))
                continue

            if not profile.artists:
                print_warning("Skipping - no artists")
                results.append(ProfileCheckResult(
                    profile_name=profile.name,
                    status=CheckStatus.SKIPPED,
                    error_message="No artists"
                ))
                continue

            result = self.checker.check_profile(profile, silent=False)
            results.append(result)
            total_added += result.total_tracks_added

            if idx < len(profiles) - 1:
                time.sleep(1)

        print()
        _print_section_header("Summary")

        with_new = sum(1 for r in results if r.total_tracks_added > 0)
        print_success(f"Checked {len(results)} profiles")
        print_success(f"Profiles with new releases: {with_new}")
        print_success(f"Total tracks added: {total_added}")

        return results


class ScheduledChecker:
    """Handles scheduled/automatic release checking."""

    def __init__(
        self,
        spotify_api: SpotifyAPI,
        playlist_ops: PlaylistOperations,
        config_manager: ConfigManager
    ):
        self.checker = ReleaseChecker(spotify_api, playlist_ops, config_manager)
        self.config_manager = config_manager

    def get_profiles_due(self) -> List[Profile]:
        """Get profiles that are due for a check."""
        due = []
        now = time.time()

        for profile in self.config_manager.config.profiles:
            if not profile.playlist_uri or not profile.artists:
                continue

            interval_seconds = profile.check_interval * 3600

            if profile.last_check is None:
                due.append(profile)
            elif now - profile.last_check >= interval_seconds:
                due.append(profile)

        return due

    def check_due_profiles(self, silent: bool = True) -> List[ProfileCheckResult]:
        """Check all profiles that are due."""
        due = self.get_profiles_due()

        if not due:
            if not silent:
                print_info("No profiles due for checking")
            return []

        if not silent:
            print_info(f"Checking {len(due)} due profiles...")

        results = []

        for profile in due:
            if not silent:
                print_info(f"Checking: {profile.name}")

            result = self.checker.check_profile(profile, silent=silent)
            results.append(result)

            if not silent and result.total_tracks_added > 0:
                print_success(f"  Added {result.total_tracks_added} tracks")

        return results

    def display_schedule(self):
        """Display check schedule for all profiles."""
        profiles = self.config_manager.config.profiles
        now = time.time()

        if RICH_AVAILABLE:
            from rich.table import Table

            table = Table(title="Check Schedule", show_header=True, header_style="bold cyan")
            table.add_column("Profile", style="white")
            table.add_column("Interval", justify="right")
            table.add_column("Last Check")
            table.add_column("Next Check")
            table.add_column("Status")

            for profile in profiles:
                interval_str = f"{profile.check_interval}h"

                if profile.last_check:
                    last_dt = datetime.fromtimestamp(profile.last_check)
                    last_str = last_dt.strftime("%Y-%m-%d %H:%M")
                    next_check = profile.last_check + (profile.check_interval * 3600)
                    next_dt = datetime.fromtimestamp(next_check)

                    if next_check <= now:
                        next_str = "[bold red]Due now[/]"
                        status = "[yellow]●[/]"
                    else:
                        next_str = next_dt.strftime("%Y-%m-%d %H:%M")
                        status = "[green]●[/]"
                else:
                    last_str = "[dim]Never[/]"
                    next_str = "[bold red]Due now[/]"
                    status = "[yellow]●[/]"

                if not profile.playlist_uri:
                    status = "[red]○[/]"
                    next_str = "[dim]No playlist[/]"
                elif not profile.artists:
                    status = "[red]○[/]"
                    next_str = "[dim]No artists[/]"

                table.add_row(profile.name, interval_str, last_str, next_str, status)

            console.print(table)
        else:
            print("\n=== Check Schedule ===")
            for profile in profiles:
                if profile.last_check:
                    last_dt = datetime.fromtimestamp(profile.last_check)
                    last_str = last_dt.strftime("%Y-%m-%d %H:%M")
                else:
                    last_str = "Never"
                status = "Ready" if profile.playlist_uri and profile.artists else "Not configured"
                print(f"  {profile.name}:")
                print(f"    Interval: {profile.check_interval}h")
                print(f"    Last check: {last_str}")
                print(f"    Status: {status}")
            print()
