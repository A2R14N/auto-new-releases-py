"""Presentation for release-check headers, counts, and playlist status messages."""

import re

from .constants import RICH_AVAILABLE, console

ACTIVE = "#38bdf8"
DETAIL = "#94a3b8"
QUIET = "#64748b"
COMPLETE = "#4ade80"
NUMBER = "bold white"


def _print_status(message: str, color: str) -> None:
    if not RICH_AVAILABLE:
        print(message)
        return

    from rich.text import Text

    line = Text(message, style=color)
    for match in re.finditer(r"(?<!\w)\d[\d,]*(?!\w)", message):
        line.stylize("bold", match.start(), match.end())
    console.print(line)


def print_activity(message: str) -> None:
    _print_status(message, ACTIVE)


def print_detail(message: str) -> None:
    _print_status(message, DETAIL)


def print_complete(message: str) -> None:
    _print_status(message, COMPLETE)


def print_profile_header(title: str, position: str = "") -> None:
    """Align the profile counter to the right without interpreting names as markup."""
    if not RICH_AVAILABLE:
        gap = max(2, 64 - len(title) - len(position))
        print(title + (" " * gap + position if position else ""))
        print("-" * 64)
        return

    from rich.cells import cell_len
    from rich.text import Text

    width = min(64, console.width)
    heading = Text(title, style=f"bold {ACTIVE}")
    gap = width - cell_len(title) - cell_len(position)
    if position and gap >= 2:
        heading.append(" " * gap)
        heading.append(position, style=QUIET)
        console.print(heading, width=width)
    else:
        console.print(heading, width=width)
        if position:
            console.print(Text(position, style=QUIET, justify="right"), width=width)
    console.print("─" * width, style=QUIET)


def print_profile_counts(track_count: int, artist_count: int) -> None:
    counts = (f"{track_count:,}", f"{artist_count:,}")
    width = max(4, *(len(count) for count in counts))
    rows = (("Playlist", counts[0], "tracks"), ("Artists", counts[1], "to check"))
    for label, count, description in rows:
        if RICH_AVAILABLE:
            from rich.text import Text

            line = Text(f"{label:<8} ", style=DETAIL)
            line.append(f"{count:>{width}}", style=NUMBER)
            line.append(f" {description}", style=DETAIL)
            console.print(line)
        else:
            print(f"{label:<8} {count:>{width}} {description}")


def print_artist_progress(current: int, total: int, artist_name: str) -> None:
    counter = f"{current:>{len(str(total))}}/{total}"
    if RICH_AVAILABLE:
        from rich.text import Text

        line = Text("  ")
        line.append(counter, style=NUMBER)
        line.append(f"  {artist_name}", style=DETAIL)
        console.print(line)
    else:
        print(f"  {counter}  {artist_name}")


def print_check_summary(profile_name: str, artist_count: int, playlist_name: str, days_to_check: int) -> None:
    rows = (
        ("Profile:", profile_name, f"bold {ACTIVE}"),
        ("Artists:", f"{artist_count:,}", NUMBER),
        ("Playlist:", playlist_name, "white"),
        ("Days to check:", str(days_to_check) if days_to_check > 0 else "All time", NUMBER),
    )
    for label, value, style in rows:
        if RICH_AVAILABLE:
            from rich.text import Text

            line = Text(f"{label:<14} ", style=DETAIL)
            line.append(value, style=style)
            console.print(line)
        else:
            print(f"{label:<14} {value}")


def print_profiles_summary(profiles) -> None:
    """Show the profiles to check in an aligned, plain-text-safe table."""
    count = len(profiles)
    print_profile_header("Check all profiles", f"{count} {'profile' if count == 1 else 'profiles'}")
    rows = [(p.name, f"{len(p.artists):,}", p.playlist_name or "No playlist") for p in profiles]
    if RICH_AVAILABLE:
        from rich.table import Table
        from rich.text import Text

        table = Table(
            box=None, show_edge=False, pad_edge=False, padding=(0, 3, 0, 0),
            header_style=DETAIL, width=min(64, console.width),
        )
        table.add_column("Profile", style=f"bold {ACTIVE}", ratio=2)
        table.add_column("Artists", style=NUMBER, justify="right", no_wrap=True)
        table.add_column("Playlist", style="white", ratio=3)
        table.add_row("", "", "")
        for name, artists, playlist in rows:
            table.add_row(Text(name), Text(artists), Text(playlist, style=QUIET if playlist == "No playlist" else "white"))
        console.print(table)
    else:
        name_width = max([len("Profile")] + [len(row[0]) for row in rows])
        count_width = max([len("Artists")] + [len(row[1]) for row in rows])
        print(f"{'Profile':<{name_width}}   {'Artists':>{count_width}}   Playlist")
        print()
        for name, artists, playlist in rows:
            print(f"{name:<{name_width}}   {artists:>{count_width}}   {playlist}")
    print()


def print_all_check_summary(
    profile_count: int, with_new: int, tracks_added: int,
    *, errors: int = 0, partial: int = 0, skipped: int = 0,
) -> None:
    """Display aligned results and a status that accounts for unfinished checks."""
    profiles_label = "profile" if profile_count == 1 else "profiles"
    print_profile_header("Check complete", f"{profile_count} {profiles_label}")
    rows = [
        ("Profiles with new releases", with_new, COMPLETE),
        ("Tracks added", tracks_added, COMPLETE),
    ]
    if errors:
        rows.append(("Profiles with errors", errors, "red"))
    if partial:
        rows.append(("Incomplete profiles", partial, "yellow"))
    if skipped:
        rows.append(("Profiles skipped", skipped, "yellow"))

    if RICH_AVAILABLE:
        from rich.table import Table
        from rich.text import Text

        table = Table(
            box=None, show_header=False, show_edge=False, pad_edge=False,
            padding=(0, 0), width=min(64, console.width),
        )
        table.add_column(style=DETAIL, ratio=1)
        table.add_column(justify="right", no_wrap=True)
        for label, count, color in rows:
            table.add_row(Text(label), Text(f"{count:,}", style=f"bold {color}" if count else QUIET))
        console.print(table)
    else:
        for label, count, _ in rows:
            value = f"{count:,}"
            print(label + " " * max(2, 64 - len(label) - len(value)) + value)

    print()
    if errors or partial:
        _print_status("⚠ Some profiles need attention", "yellow")
    elif skipped:
        _print_status("⚠ Check finished with skipped profiles", "yellow")
    elif tracks_added:
        track_label = "track" if tracks_added == 1 else "tracks"
        profile_label = "profile" if with_new == 1 else "profiles"
        print_complete(f"✓ Added {tracks_added:,} {track_label} across {with_new} {profile_label}")
    else:
        print_complete("✓ Everything is up to date")
