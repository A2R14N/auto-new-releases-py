"""
Core logic for checking new releases from tracked artists,
processing them, and adding to playlists.
"""

import time
import math
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Set, Callable

from .constants import (
    RICH_AVAILABLE, console,
    print_success, print_error, print_warning, print_info,
)
from .output import (
    print_activity, print_profile_header, print_profile_counts, print_artist_progress,
    print_check_summary, print_profiles_summary, print_all_check_summary,
    open_block, data_table, suppress_routine_output,
)
from .models import Artist, Profile
from .api import SpotifyAPI, SpotifyAPIError, ReleaseFetcher, transient_read_error
from .config import ConfigManager
from .playlist import PlaylistOperations, PlaylistBackup, _is_bridge
from .filters import RemixDetector, ReleaseDateFilter


def _print_section_header(title: str, position: str = "") -> None:
    print_profile_header(title, position)


def _print_artist_progress(current: int, total: int, artist_name: str) -> None:
    print_artist_progress(current, total, artist_name)


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
    artists_pending: int = 0
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
        if self.status == CheckStatus.PARTIAL:
            return f"{self.artists_checked}/{self.total_artists} artists checked, {self.artists_pending} pending; added {self.total_tracks_added} tracks. {self.error_message}"
        if self.total_tracks_added == 0:
            return f"No new releases found for {self.artists_checked} artists"
        parts = [f"Added {self.total_tracks_added} tracks", f"from {self.artists_with_new} artists"]
        if self.total_tracks_filtered > 0:
            parts.append(f"({self.total_tracks_filtered} filtered)")
        return " ".join(parts)

    def display(self):
        """Display detailed results."""
        if RICH_AVAILABLE:
            status_colors = {
                CheckStatus.SUCCESS: 'anr.success',
                CheckStatus.PARTIAL: 'anr.warning',
                CheckStatus.NO_NEW: 'anr.text',
                CheckStatus.ERROR: 'anr.error',
                CheckStatus.SKIPPED: 'anr.secondary',
            }
            color = status_colors.get(self.status, 'anr.text')

            lines = [
                f"[bold]Status:[/] [{color}]{self.status.value.upper()}[/]",
                f"[bold]Duration:[/] {self.duration_seconds:.1f}s",
                "",
                f"[bold]Artists Checked:[/] {self.artists_checked}/{self.total_artists}",
                f"[bold]Artists with New Releases:[/] {self.artists_with_new}",
                f"[bold]Total Releases Found:[/] {self.total_releases}",
                "",
                f"[anr.heading]Tracks Added:[/] {self.total_tracks_added}",
                f"[anr.heading]Tracks Filtered:[/] {self.total_tracks_filtered}",
            ]

            if self.error_message:
                lines.append(f"\n[anr.error]Error:[/] {self.error_message}")

            console.print(open_block(f"[bold]Check Results: {self.profile_name}[/]", "\n".join(lines)))

            if self.artist_results and any(r.tracks_added > 0 for r in self.artist_results):
                table = data_table(title="Artist Breakdown", show_header=True, header_style='anr.heading')
                table.add_column("Artist", style='anr.text')
                table.add_column("Releases", justify="right")
                table.add_column("Added", justify="right", style='anr.text')
                table.add_column("Filtered", justify="right", style='anr.text')

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
    tracks_added: int = 0
    artists_checked: int = 0
    artists_pending: int = 0


ProgressCallback = Callable[[CheckProgress], None]


def check_profiles_with_progress(checker, profiles, *, dry_run=False, enabled=True, interactive=False):
    """Check each profile once while keeping one overview of the whole run."""
    from .check_display import CheckAllDisplay

    profiles = [profile for profile in profiles if profile.enabled]
    if not profiles:
        if enabled:
            print_info('No enabled profiles to check.')
        return []
    display = CheckAllDisplay(profiles, console=console, enabled=enabled,
                              dry_run=dry_run, interactive=interactive)
    results = []
    with display:
        for index, profile in enumerate(profiles):
            display.start_profile(index)
            reason = 'No playlist configured' if not profile.playlist_uri else 'No artists' if not profile.artists else ''
            if reason:
                result = ProfileCheckResult(profile.name, CheckStatus.SKIPPED, error_message=reason)
            else:
                result = checker.check_profile(profile, progress_callback=display.update if enabled else None,
                                               silent=True, dry_run=dry_run)
            results.append(result)
            display.complete_profile(result)
        display.finish()
    display.report_issues()
    return results


def check_with_progress(checker, profile: Profile, *, dry_run: bool = False,
                        enabled: bool = True) -> ProfileCheckResult:
    """Use the same progress display for interactive and command-line checks."""
    if not enabled:
        return checker.check_profile(profile, silent=True, dry_run=dry_run)

    if not RICH_AVAILABLE or not console.is_terminal:
        def simple_progress(p: CheckProgress):
            if p.phase == 'artist':
                print_artist_progress(p.artist_current, p.artist_total, p.artist_name)
            elif p.message and p.phase != 'done':
                print_info(p.message)

        return checker.check_profile(profile, progress_callback=simple_progress,
                                     silent=True, dry_run=dry_run)

    from rich.progress import Progress, BarColumn, TaskProgressColumn
    from rich.console import Group
    from rich.constrain import Constrain
    from rich.text import Text

    class ReleaseProgress(Progress):
        """Keep the activity, blue progress bar, and counts on separate lines."""

        def get_renderables(self):
            for task in self.tasks:
                yield Constrain(Group(
                    Text(task.description, style='anr.heading'),
                    self.make_tasks_table([task]),
                    Text(task.fields.get('status', ''), style='anr.secondary'),
                ), width=min(68, console.width))

    with ReleaseProgress(
        BarColumn(bar_width=None, style='anr.rule', complete_style='anr.accent',
                  finished_style='anr.accent', pulse_style='anr.accent'),
        TaskProgressColumn(),
        console=console, expand=True,
    ) as progress:
        task = progress.add_task('Preparing check', total=None, status='')

        def update_progress(p: CheckProgress):
            if p.phase == 'init':
                progress.update(task, description='Reading playlist', status='')
            elif p.phase == 'artist':
                # Artist events arrive before its API work starts.
                progress.update(task, completed=p.artist_current - 1, total=p.artist_total,
                                description=f'Checking {p.artist_name}',
                                status=f'{p.artist_current}/{p.artist_total} artists')
            elif p.phase == 'retry':
                progress.update(task, description=f'Retrying {p.artist_name}', status='Pending recovery')
            elif p.phase in ('adding', 'sorting'):
                current = progress.tasks[task]
                progress.update(task, completed=current.total or 0,
                                description='Adding tracks' if p.phase == 'adding' else 'Sorting playlist',
                                status=p.message if p.phase == 'adding' else '')

        result = checker.check_profile(profile, progress_callback=update_progress,
                                       silent=True, dry_run=dry_run)
        description = 'Check complete'
        if result.status == CheckStatus.ERROR:
            description = 'Check failed'
        elif result.status == CheckStatus.PARTIAL:
            description = 'Check incomplete'
        status = f'{result.artists_checked}/{result.total_artists} artists'
        if result.artists_pending:
            status += f' · {result.artists_pending} pending'
        progress.update(task, completed=result.artists_checked,
                        total=max(result.total_artists, 1), description=description, status=status)
        return result


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
        silent: bool = False,
        dry_run: bool = False,
        recovery_due_only: bool = False,
    ) -> ProfileCheckResult:
        """Check a profile, optionally previewing without persistent mutations."""
        # Import here to avoid circular
        from .tools import PlaylistTools

        start_time = time.time()
        from .notifications import notify_profile

        def notify(event):
            if not dry_run:
                notify_profile(self.api, profile, event, result,
                               enabled=self.config_manager.config.spotify_notifications)

        result = ProfileCheckResult(
            profile_name=profile.name,
            status=CheckStatus.SUCCESS,
            total_artists=len(profile.artists)
        )

        def apply_sort():
            notify('sorting')
            if progress_callback:
                progress_callback(CheckProgress(phase='sorting', message='Sorting playlist by release date'))
            if not silent:
                print_activity("Sorting playlist by release date")
            try:
                with suppress_routine_output(enabled=silent):
                    sort_result = PlaylistTools(self.api, self.playlist_ops).sorter.sort_by_release_date(profile.playlist_uri)
                if not sort_result.success:
                    raise SpotifyAPIError(sort_result.error_message)
                return True
            except Exception as sort_error:
                result.status = CheckStatus.PARTIAL
                message = f"Playlist sort failed: {sort_error}"
                result.error_message = "; ".join(part for part in (result.error_message, message) if part)
                print_warning(message)
                return False

        if not profile.playlist_uri:
            result.status = CheckStatus.ERROR
            result.error_message = "No playlist configured"
            notify('error')
            return result

        if not profile.artists:
            result.status = CheckStatus.ERROR
            result.error_message = "No artists to check"
            notify('error')
            return result

        try:
            notify('start')
            if progress_callback:
                progress_callback(CheckProgress(phase='init', message='Fetching existing playlist tracks'))

            if profile.sort_by_date and not dry_run and not profile.pending_sort:
                pending_backup = PlaylistBackup.get_pending(profile.playlist_uri)
                if pending_backup and pending_backup.get('operation') == 'sort_by_release_date':
                    profile.pending_sort = True
                    self.config_manager.save()

            # Finish a previously failed sort before adding anything else.
            # Its original recovery backup must survive any partial rewrite.
            if profile.sort_by_date and profile.pending_sort and not dry_run:
                if not apply_sort():
                    result.duration_seconds = time.time() - start_time
                    notify('partial')
                    return result
                profile.pending_sort = False
                self.config_manager.save()

            uncertain_write = any(isinstance(entry, dict)
                and entry.get('playlist_uri') == profile.playlist_uri
                and 'Playlist addition not confirmed' in entry.get('error', '')
                for entry in (profile.pending_artists or {}).values())
            if uncertain_write and _is_bridge(self.api):
                existing_tracks = self.playlist_ops.get_playlist_tracks(profile.playlist_uri, refresh=True)
            else:
                existing_tracks = self.playlist_ops.get_playlist_tracks(profile.playlist_uri)
            existing_uris = {t.uri for t in existing_tracks}
            existing_signatures = set()
            if profile.skip_similar_duplicates:
                existing_signatures = {f"{t.name.lower()}|||{t.primary_artist.lower()}" for t in existing_tracks}

            now = time.time()
            artist_uris = {a.uri for a in profile.artists}
            old_pending = {
                uri: dict(entry) for uri, entry in (profile.pending_artists or {}).items()
                if uri in artist_uris and isinstance(entry, dict)
                and entry.get('playlist_uri') == profile.playlist_uri
            }
            full_scan = not old_pending or profile.last_scan is None or now - profile.last_scan >= profile.check_interval * 3600
            artists = list(profile.artists) if full_scan else [a for a in profile.artists if a.uri in old_pending]
            blocked = {a.uri: old_pending[a.uri] for a in artists if a.uri in old_pending and (
                old_pending[a.uri].get('not_before', 0) > now or
                (recovery_due_only and not full_scan and old_pending[a.uri].get('retry_at', 0) > now))}
            artists = [a for a in artists if a.uri not in blocked]
            result.total_artists = len(artists) + len(blocked)
            if not silent:
                print_profile_counts(len(existing_uris), len(artists))
                print()

            tracked_tracks = profile.tracked_tracks or {}
            tracked_releases = profile.tracked_releases or {}
            cutoffs = {}
            for artist in artists:
                cutoff = old_pending.get(artist.uri, {}).get('cutoff', now - profile.days_to_check * 86400 if profile.days_to_check else None)
                if cutoff is not None and (not isinstance(cutoff, (int, float)) or not math.isfinite(cutoff)):
                    cutoff = now - profile.days_to_check * 86400 if profile.days_to_check else None
                cutoffs[artist.uri] = cutoff

            all_new_tracks = []
            candidate_tracks = set()
            candidate_signatures = set()
            candidate_releases = set()
            processed_releases = set()
            filtered_tracks = set()
            completed_releases = set()
            release_owners = {}
            encountered_releases = set()
            artist_reads = {}
            failures = {}
            failed_at = {}
            scan_cooldown = 0.0
            artist_results = {a.uri: ArtistCheckResult(artist=a, status=CheckStatus.SUCCESS) for a in artists}

            def signature(track):
                names = track.get('artists') or []
                primary = names[0].get('name', '') if names else ''
                return f"{track.get('name', '').lower()}|||{primary.lower()}"

            def process_release(release, artist):
                uri = release.get('uri')
                if not uri:
                    raise SpotifyAPIError('Artist release is missing its URI')
                release_owners.setdefault(uri, set()).add(artist.uri)
                if uri in tracked_releases or uri in completed_releases:
                    return
                if uri not in encountered_releases:
                    encountered_releases.add(uri)
                    result.total_releases += 1
                    artist_results[artist.uri].releases_found += 1

                if scan_cooldown > time.monotonic():
                    raise SpotifyAPIError('Spotify cooldown; release pending recovery',
                        http_status=429, retry_after=scan_cooldown - time.monotonic())
                album = self.api.get_album(uri)
                if not isinstance(album, dict) or not isinstance(album.get('tracks', {}).get('items'), list):
                    raise SpotifyAPIError(f'Could not read complete album {uri}', retryable=True)
                tracks = album['tracks']['items']
                if any(not isinstance(t, dict) or not t.get('uri') for t in tracks):
                    raise SpotifyAPIError(f'Album contains unreadable tracks: {uri}')
                popularity = album.get('popularity')
                if profile.skip_low_popularity and popularity is None:
                    raise SpotifyAPIError('Spotify did not provide album popularity. Disable the low-popularity filter for this profile.', retryable=False)
                if ((profile.skip_long_albums and len(tracks) > profile.max_songs)
                        or (profile.skip_low_popularity and popularity < profile.min_popularity)):
                    processed_releases.add(uri)
                    completed_releases.add(uri)
                    artist_results[artist.uri].tracks_filtered += len(tracks)
                    result.total_tracks_filtered += len(tracks)
                    return

                # Keep changes local until this release has complete selection data.
                eligible = {}
                rejected = set()
                dependencies = False
                filtered_count = 0
                for track in tracks:
                    track_uri = track['uri']
                    key = signature(track)
                    if track_uri in existing_uris or track_uri in tracked_tracks or track_uri in filtered_tracks:
                        filtered_count += 1
                    elif track_uri in candidate_tracks:
                        dependencies = True
                        filtered_count += 1
                    elif profile.skip_similar_duplicates and key in existing_signatures:
                        filtered_count += 1
                    elif profile.skip_similar_duplicates and key in candidate_signatures:
                        dependencies = True
                        filtered_count += 1
                    elif profile.skip_remixes and RemixDetector.is_remix_or_variant(track.get('name', ''), release.get('name', '')):
                        rejected.add(track_uri)
                        filtered_count += 1
                    elif track_uri not in eligible:
                        eligible[track_uri] = track
                ordered = list(eligible.values())
                if profile.limit_songs_per_album and len(ordered) > profile.max_songs_per_album:
                    details = self.api.get_multiple_tracks(list(eligible))
                    by_uri = {t.get('uri'): t for t in (details or []) if isinstance(t, dict)}
                    if set(by_uri) != set(eligible):
                        raise SpotifyAPIError(f'Incomplete track ranking data for {uri}', retryable=True)
                    if any(type(t.get('popularity')) not in (int, float) or not math.isfinite(t['popularity']) for t in by_uri.values()):
                        raise SpotifyAPIError('Spotify did not provide track popularity. Disable the per-album popularity limit for this profile.', retryable=False)
                    ordered.sort(key=lambda t: by_uri[t['uri']]['popularity'], reverse=True)

                selected = []
                selected_signatures = set()
                for track in ordered:
                    key = signature(track)
                    if profile.skip_similar_duplicates and key in selected_signatures:
                        filtered_count += 1
                    elif profile.limit_songs_per_album and len(selected) >= profile.max_songs_per_album:
                        rejected.add(track['uri'])
                        filtered_count += 1
                    else:
                        selected.append(track)
                        selected_signatures.add(key)
                uris = [t['uri'] for t in selected]
                all_new_tracks.extend(uris)
                candidate_tracks.update(uris)
                candidate_signatures.update(selected_signatures)
                filtered_tracks.update(rejected)
                artist_results[artist.uri].tracks_added += len(uris)
                artist_results[artist.uri].tracks_filtered += filtered_count
                result.total_tracks_filtered += filtered_count
                if uris or dependencies:
                    candidate_releases.add(uri)
                else:
                    processed_releases.add(uri)
                completed_releases.add(uri)
                if not silent:
                    print_info(f"  Release: {release.get('name', 'Unknown')}")
                    for track in selected:
                        print_success(f"    [adding] {track.get('name', '')}")

            def scan_artist(artist, entry=None):
                nonlocal scan_cooldown
                errors = []
                try:
                    if artist.uri not in artist_reads:
                        if entry is not None and entry.get('_exception') is not None:
                            raise entry['_exception']
                        if entry is not None and entry.get('error'):
                            raise SpotifyAPIError.from_bridge(entry['error'], entry.get('error_details'))
                        if entry is None and scan_cooldown > time.monotonic():
                            raise SpotifyAPIError('Spotify cooldown; artist pending recovery',
                                http_status=429, retry_after=scan_cooldown - time.monotonic())
                        releases = entry['releases'] if entry is not None else self.release_fetcher.get_artist_releases(artist.uri)
                        cutoff = cutoffs[artist.uri]
                        artist_reads[artist.uri] = ReleaseDateFilter.filter_by_days(
                            releases, 1 if cutoff is not None else 0,
                            cutoff=datetime.fromtimestamp(cutoff) if cutoff is not None else None)
                    for release in artist_reads[artist.uri]:
                        try:
                            process_release(release, artist)
                        except Exception as error:
                            errors.append(error)
                except Exception as error:
                    errors.append(error)
                if errors:
                    failures[artist.uri] = errors
                    failed_at[artist.uri] = time.monotonic()
                    for error in errors:
                        if getattr(error, 'http_status', None) == 429:
                            scan_cooldown = max(scan_cooldown, time.monotonic() + (getattr(error, 'retry_after', None) or 1))
                else:
                    failures.pop(artist.uri, None)

            batch_read = getattr(self.release_fetcher, 'get_artist_releases_batch', None)
            prefetched = {}
            for idx, artist in enumerate(artists):
                if progress_callback:
                    progress_callback(CheckProgress(phase='artist', artist_current=idx + 1,
                        artist_total=len(artists), artist_name=artist.name, message=f'Checking: {artist.name}'))
                if not silent:
                    _print_artist_progress(idx + 1, len(artists), artist.name)
                if idx % 4 == 0:
                    prefetched = {}
                    if scan_cooldown > time.monotonic():
                        error = SpotifyAPIError('Spotify cooldown; artist pending recovery',
                            http_status=429, retry_after=scan_cooldown - time.monotonic())
                        prefetched = {a.uri: {'_exception': error} for a in artists[idx:idx + 4]}
                    elif callable(batch_read):
                        try:
                            prefetched = batch_read([a.uri for a in artists[idx:idx + 4]])
                        except Exception as error:
                            if 'unknown method' not in str(error).lower():
                                prefetched = {a.uri: {'_exception': error} for a in artists[idx:idx + 4]}
                scan_artist(artist, prefetched.get(artist.uri))
                if progress_callback:
                    progress_callback(CheckProgress(
                        phase='artist_done', artist_current=idx + 1, artist_total=len(artists),
                        artist_name=artist.name, artists_checked=idx + 1 - len(failures),
                        artists_pending=len(failures) + len(blocked)))

            # A single failed-item pass, outside the original RPC. Healthy runs
            # incur no waiting. Spotipy already owns its request retries.
            retry_artists = []
            shared_cooldown = 0
            for artist in artists:
                errors = failures.get(artist.uri, [])
                if not errors:
                    continue
                delay = max((getattr(e, 'retry_after', None) or 1) for e in errors)
                if any(getattr(e, 'http_status', None) == 429 for e in errors):
                    shared_cooldown = max(shared_cooldown, failed_at[artist.uri] + delay)
                if not isinstance(self.api, SpotifyAPI) and all(transient_read_error(e) for e in errors) and delay <= 2:
                    retry_artists.append((artist, failed_at[artist.uri] + delay))
            if retry_artists and shared_cooldown - time.monotonic() <= 2:
                ready = max(shared_cooldown, max(ready for _, ready in retry_artists))
                if progress_callback:
                    progress_callback(CheckProgress(phase='retry_wait',
                        message=f'Waiting to retry {len(retry_artists)} artists',
                        artists_checked=len(artists) - len(failures),
                        artists_pending=len(failures) + len(blocked)))
                time.sleep(max(0, ready - time.monotonic()))
                if not silent:
                    print_activity(f'Retrying {len(retry_artists)} pending artists')
                for artist, _ in retry_artists:
                    if progress_callback:
                        progress_callback(CheckProgress(phase='retry', artist_name=artist.name,
                            message=f'Retrying: {artist.name}', artists_checked=len(artists) - len(failures),
                            artists_pending=len(failures) + len(blocked)))
                    scan_artist(artist)
                    if progress_callback:
                        progress_callback(CheckProgress(phase='retry_done', artist_name=artist.name,
                            artists_checked=len(artists) - len(failures),
                            artists_pending=len(failures) + len(blocked)))

            added = failed = 0
            result.total_tracks_added = len(all_new_tracks) if dry_run else 0
            if all_new_tracks and not dry_run:
                if progress_callback:
                    progress_callback(CheckProgress(phase='adding', tracks_found=len(all_new_tracks), message=f'Adding {len(all_new_tracks)} tracks to playlist'))
                added, failed = self.playlist_ops.add_tracks(profile.playlist_uri, all_new_tracks)
                result.total_tracks_added = added
                if progress_callback:
                    progress_callback(CheckProgress(phase='added', tracks_added=added))
                if failed or added != len(all_new_tracks):
                    result.status = CheckStatus.PARTIAL
                    result.error_message = f'Failed to confirm {len(all_new_tracks) - added} track additions'
                    for uri in candidate_releases:
                        for owner in release_owners[uri]:
                            failures.setdefault(owner, []).append(SpotifyAPIError('Playlist addition not confirmed; pending recovery', retryable=False))
                if profile.sort_by_date and added > 0:
                    profile.pending_sort = True
                    self.config_manager.save()
                    if apply_sort():
                        profile.pending_sort = False
            elif not all_new_tracks:
                result.status = CheckStatus.NO_NEW

            pending = dict(blocked)
            cooldown = max((getattr(e, 'retry_after', None) or 0
                            for errors in failures.values() for e in errors
                            if getattr(e, 'http_status', None) == 429), default=0)
            for artist in artists:
                row = artist_results[artist.uri]
                errors = failures.get(artist.uri, [])
                if errors:
                    row.status = CheckStatus.ERROR
                    row.error_message = '; '.join(dict.fromkeys(str(e) for e in errors))
                    print_warning(f'    Could not check {artist.name}: {row.error_message}')
                    pending[artist.uri] = {
                        'cutoff': cutoffs[artist.uri], 'playlist_uri': profile.playlist_uri,
                        'error': row.error_message,
                        'retry_at': time.time() + max(300, max(getattr(e, 'retry_after', None) or 0 for e in errors)),
                        'not_before': time.time() + cooldown if cooldown else 0,
                    }
                result.artist_results.append(row)
            result.artists_pending = len(pending)
            result.artists_checked = result.total_artists - len(pending)
            result.artists_with_new = sum(r.tracks_added > 0 for r in result.artist_results)
            if pending:
                result.status = CheckStatus.ERROR if len(pending) == result.total_artists and not all_new_tracks else CheckStatus.PARTIAL
                message = f'Could not check {len(pending)} of {result.total_artists} artists; {result.artists_checked}/{result.total_artists} checked, {len(pending)} pending'
                reasons = '; '.join(f'{a.name}: {artist_results[a.uri].error_message}' for a in artists if a.uri in pending)
                if blocked:
                    reasons += '; waiting for scheduled recovery or Spotify cooldown'
                result.error_message = '; '.join(p for p in (result.error_message, message, reasons) if p)
            if not silent:
                print_info(f'{result.artists_checked}/{result.total_artists} artists checked, {len(pending)} pending')

            if not dry_run:
                processed_at = time.time()
                profile.tracked_tracks = profile.tracked_tracks or {}
                profile.tracked_releases = profile.tracked_releases or {}
                for uri in filtered_tracks:
                    profile.tracked_tracks[uri] = processed_at
                for uri in processed_releases:
                    profile.tracked_releases[uri] = processed_at
                if all_new_tracks and not failed and added == len(all_new_tracks):
                    for uri in candidate_tracks:
                        profile.tracked_tracks[uri] = processed_at
                    for uri in candidate_releases:
                        profile.tracked_releases[uri] = processed_at
                profile.pending_artists = pending
                if full_scan:
                    profile.last_scan = now
                if not pending and not failed and not (profile.sort_by_date and profile.pending_sort):
                    profile.last_check = processed_at
                self.config_manager.save()


            result.duration_seconds = time.time() - start_time

            if progress_callback:
                progress_callback(CheckProgress(
                    phase='done',
                    tracks_found=result.total_tracks_added,
                    message='Check complete!', tracks_added=result.total_tracks_added,
                    artists_checked=result.artists_checked, artists_pending=result.artists_pending,
                ))

            notify(result.status.value)
            return result

        except Exception as e:
            result.status = CheckStatus.ERROR
            result.error_message = str(e)
            result.duration_seconds = time.time() - start_time
            traceback.print_exc()
            notify('error')
            return result

    def check_all_profiles(
        self,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
        silent: bool = False
    ) -> List[ProfileCheckResult]:
        """Check all profiles for new releases."""
        profiles = [profile for profile in self.config_manager.config.profiles if profile.enabled]
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
        print_check_summary(
            profile.name, len(profile.artists), profile.playlist_name, profile.days_to_check,
        )
        print()

        if RICH_AVAILABLE:
            from rich.prompt import Confirm
            if not Confirm.ask("Start check?", default=True, console=console):
                print_warning("Cancelled")
                return ProfileCheckResult(profile_name=profile.name, status=CheckStatus.SKIPPED)
        else:
            if input("Start check? [Y/n]: ").lower() == 'n':
                print_warning("Cancelled")
                return ProfileCheckResult(profile_name=profile.name, status=CheckStatus.SKIPPED)

        print()

        result = check_with_progress(self.checker, profile)

        print()
        result.display()
        return result

    def run_check_all(self) -> List[ProfileCheckResult]:
        """Run check on all profiles interactively."""
        profiles = [profile for profile in self.config_manager.config.profiles if profile.enabled]

        if not profiles:
            print_info("No enabled profiles to check.")
            return []

        print_profiles_summary(profiles)

        if RICH_AVAILABLE:
            from rich.prompt import Confirm
            if not Confirm.ask("Check all profiles?", default=True, console=console):
                print_warning("Cancelled")
                return []
        else:
            if input("Check all profiles? [Y/n]: ").lower() == 'n':
                print_warning("Cancelled")
                return []

        if RICH_AVAILABLE and console.is_terminal:
            console.clear()
        return check_profiles_with_progress(self.checker, profiles, interactive=True)



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
            if not profile.enabled or not profile.playlist_uri or not profile.artists:
                continue

            interval_seconds = profile.check_interval * 3600
            last_scan = profile.last_scan if profile.last_scan is not None else profile.last_check
            pending_due = any(
                isinstance(entry, dict) and entry.get('playlist_uri') == profile.playlist_uri
                and max(entry.get('retry_at', 0), entry.get('not_before', 0)) <= now
                for uri, entry in (profile.pending_artists or {}).items()
                if any(a.uri == uri for a in profile.artists)
            )
            if last_scan is None or (profile.sort_by_date and profile.pending_sort) or pending_due:
                due.append(profile)
            elif now - last_scan >= interval_seconds:
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
            print_info(f"Checking {len(due)} due profiles")

        results = []

        for profile in due:
            if not silent:
                print_info(f"Checking: {profile.name}")

            result = self.checker.check_profile(profile, silent=silent, recovery_due_only=True)
            results.append(result)

            if not silent and result.total_tracks_added > 0:
                print_success(f"  Added {result.total_tracks_added} tracks")

        return results

    def display_schedule(self):
        """Display check schedule for all profiles."""
        profiles = self.config_manager.config.profiles
        now = time.time()

        if RICH_AVAILABLE:

            table = data_table(title="Check Schedule", show_header=True, header_style='anr.heading')
            table.add_column("Profile", style='anr.text')
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

                    if next_check <= now or (profile.sort_by_date and profile.pending_sort):
                        next_str = "Due now"
                        status = "[anr.warning]●[/]"
                    else:
                        next_str = next_dt.strftime("%Y-%m-%d %H:%M")
                        status = "[anr.success]●[/]"
                else:
                    last_str = "[anr.secondary]Never[/]"
                    next_str = "Due now"
                    status = "[anr.warning]●[/]"

                if profile.last_scan is not None:
                    next_check = profile.last_scan + profile.check_interval * 3600
                    retry_times = [max(entry.get('retry_at', 0), entry.get('not_before', 0))
                        for uri, entry in (profile.pending_artists or {}).items()
                        if isinstance(entry, dict) and entry.get('playlist_uri') == profile.playlist_uri
                        and any(a.uri == uri for a in profile.artists)]
                    if retry_times:
                        next_check = min(next_check, min(retry_times))
                    if profile.sort_by_date and profile.pending_sort:
                        next_check = now
                    next_str = 'Due now' if next_check <= now else datetime.fromtimestamp(next_check).strftime('%Y-%m-%d %H:%M')
                    status = f'[anr.warning]{len(retry_times)} pending[/]' if retry_times else '[anr.success]●[/]'

                if not profile.playlist_uri:
                    status = "[anr.error]○[/]"
                    next_str = "[anr.secondary]No playlist[/]"
                elif not profile.artists:
                    status = "[anr.error]○[/]"
                    next_str = "[anr.secondary]No artists[/]"

                if not profile.enabled:
                    next_str = '—'
                    status = '[anr.secondary]Disabled[/]'
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
                status = 'Disabled' if not profile.enabled else ('Ready' if profile.playlist_uri and profile.artists else 'Not configured')
                print(f"  {profile.name}:")
                print(f"    Interval: {profile.check_interval}h")
                print(f"    Last check: {last_str}")
                print(f"    Status: {status}")
                if profile.pending_artists:
                    print(f"    Pending artists: {len(profile.pending_artists)}")
            print()
