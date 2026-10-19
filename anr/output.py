"""Shared open layouts, menu typography, tables, and release-check output."""

import re
from contextlib import contextmanager
from contextvars import ContextVar

from .constants import RICH_AVAILABLE, console

ACTIVE = 'anr.heading'
DETAIL = 'anr.secondary'
QUIET = 'anr.secondary'
COMPLETE = 'anr.text'
NUMBER = 'anr.heading'

_routine_output_suppressed = ContextVar('anr_routine_output_suppressed', default=False)


@contextmanager
def suppress_routine_output(enabled: bool = True):
    """Let a progress display own routine output without hiding diagnostics."""
    token = _routine_output_suppressed.set(_routine_output_suppressed.get() or enabled)
    try:
        yield
    finally:
        _routine_output_suppressed.reset(token)


def detail_text(value: str):
    """Emphasize counts while keeping names, dates, and descriptions neutral."""
    from rich.text import Text

    line = Text(value, style=DETAIL)
    for match in re.finditer(r'\b\d[\d,]*(?=\s+(?:artists?|tracks?|profiles?|releases?|tracked|configured)\b)', value):
        line.stylize(NUMBER, match.start(), match.end())
    return line


def screen_width() -> int:
    return min(68, console.width)


def menu_outline(content, *, width=None):
    """Use one consistent frame for menus that navigate to other screens."""
    from rich import box
    from rich.panel import Panel

    return Panel(content, box=box.ROUNDED, border_style='anr.rule',
                 padding=(0, 2), width=min(console.width, width or screen_width()))


def data_table(*args, **kwargs):
    """Apply the same unframed table style to lists, tools, and CLI results."""
    from rich.table import Table

    options = dict(box=None, show_edge=False, pad_edge=False, title_justify='left',
                   width=screen_width(), header_style='anr.heading')
    options.update(kwargs)
    return Table(*args, **options)


def open_block(title, body, *, subtitle: str = '', right: str = '', context=None, width=None):
    """Compose an unframed Rich screen with one neutral separator."""
    from rich.console import Group
    from rich.constrain import Constrain
    from rich.padding import Padding
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text

    heading = Table.grid(expand=True, padding=0)
    heading.add_column(ratio=1)
    heading.add_column(justify='right')
    heading.add_row(title if isinstance(title, Text) else Text.from_markup(title, style=ACTIVE),
                    detail_text(right))
    parts = [heading]
    if subtitle:
        parts.append(detail_text(subtitle))
    if context is not None:
        parts.extend([Text(''), context])
    parts.extend([Text(''), Rule(style='anr.rule'), Text(''), body])
    return Constrain(Padding(Group(*parts), (1, 0)), width=min(console.width, width or screen_width()))


def menu_group(label: str, rows):
    """Keep shortcut, command, and optional hint columns aligned."""
    from rich.console import Group
    from rich.table import Table
    from rich.text import Text

    table = Table.grid(expand=True, padding=(0, 2, 0, 0))
    table.add_column(width=5, style='anr.shortcut', no_wrap=True)
    table.add_column(ratio=1)
    details = console.width >= 54
    if details:
        table.add_column(justify='right', max_width=26)
    for key, command, hint in rows:
        cells = [Text(f'[{key}]', style='anr.shortcut'), Text(command, style='anr.text')]
        if details:
            cells.append(detail_text(hint))
        table.add_row(*cells)
    return Group(Text(label, style=DETAIL), table) if label else table


def menu_footer(items):
    """Let footer shortcuts wrap as whole labels in narrow terminals."""
    from rich.columns import Columns
    from rich.text import Text

    labels = []
    for key, label in items:
        item = Text(f'[{key}]', style='anr.shortcut')
        item.append(f' {label}', style='anr.text')
        labels.append(item)
    return Columns(labels, padding=(0, 3), expand=False)


def _print_status(message: str, color: str) -> None:
    if _routine_output_suppressed.get():
        return
    if not RICH_AVAILABLE:
        print(message)
        return

    from rich.text import Text

    marker_styles = {'✓': 'anr.success', '⚠': 'anr.warning', '✗': 'anr.error'}
    if message and message[0] in marker_styles:
        line = Text(message[0], style=marker_styles[message[0]])
        line.append(message[1:], style='anr.text')
    else:
        line = Text(message, style=color)
    for match in re.finditer(r"(?<!\w)\d[\d,]*(?!\w)", message):
        line.stylize("bold", match.start(), match.end())
    console.print(line)


def print_activity(message: str) -> None:
    _print_status(message, ACTIVE)


def print_detail(message: str) -> None:
    _print_status(message, DETAIL)


def print_complete(message: str) -> None:
    _print_status(message if message.startswith('✓') else '✓ ' + message, COMPLETE)


def print_profile_header(title: str, position: str = "") -> None:
    """Align the profile counter to the right without interpreting names as markup."""
    if not RICH_AVAILABLE:
        gap = max(2, 68 - len(title) - len(position))
        print(title + (" " * gap + position if position else ""))
        print("-" * 68)
        return

    from rich.cells import cell_len
    from rich.text import Text

    width = min(68, console.width)
    heading = Text(title, style=ACTIVE)
    gap = width - cell_len(title) - cell_len(position)
    if position and gap >= 2:
        heading.append(" " * gap)
        heading.append(position, style=QUIET)
        console.print(heading, width=width)
    else:
        console.print(heading, width=width)
        if position:
            console.print(Text(position, style=QUIET, justify="right"), width=width)
    console.print("─" * width, style='anr.rule')


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
        ("Profile:", profile_name, ACTIVE),
        ("Artists:", f"{artist_count:,}", NUMBER),
        ("Playlist:", playlist_name, 'anr.text'),
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
            header_style=DETAIL, width=min(68, console.width),
        )
        table.add_column("Profile", style=ACTIVE, ratio=2)
        table.add_column("Artists", style=NUMBER, justify="right", no_wrap=True)
        table.add_column("Playlist", style='anr.text', ratio=3)
        table.add_row("", "", "")
        for name, artists, playlist in rows:
            table.add_row(Text(name), Text(artists), Text(playlist, style=QUIET if playlist == "No playlist" else 'anr.text'))
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
        rows.append(("Profiles with errors", errors, 'anr.error'))
    if partial:
        rows.append(("Incomplete profiles", partial, 'anr.warning'))
    if skipped:
        rows.append(("Profiles skipped", skipped, 'anr.warning'))

    if RICH_AVAILABLE:
        from rich.table import Table
        from rich.text import Text

        table = Table(
            box=None, show_header=False, show_edge=False, pad_edge=False,
            padding=(0, 0), width=min(68, console.width),
        )
        table.add_column(style=DETAIL, ratio=1)
        table.add_column(justify="right", no_wrap=True)
        for label, count, color in rows:
            table.add_row(Text(label), Text(f"{count:,}", style=NUMBER if count else QUIET))
        console.print(table)
    else:
        for label, count, _ in rows:
            value = f"{count:,}"
            print(label + " " * max(2, 68 - len(label) - len(value)) + value)

    print()
    if errors or partial:
        _print_status("⚠ Some profiles need attention", 'anr.warning')
    elif skipped:
        _print_status("⚠ Check finished with skipped profiles", 'anr.warning')
    elif tracks_added:
        track_label = "track" if tracks_added == 1 else "tracks"
        profile_label = "profile" if with_new == 1 else "profiles"
        print_complete(f"✓ Added {tracks_added:,} {track_label} across {with_new} {profile_label}")
    else:
        print_complete("✓ Everything is up to date")
