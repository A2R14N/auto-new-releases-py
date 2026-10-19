"""Open terminal layouts shared by the interactive menus."""

from datetime import datetime

from .constants import APP_NAME, APP_VERSION, RICH_AVAILABLE, console
from .output import detail_text, menu_footer, menu_group, menu_outline, open_block, screen_width


def display_main_dashboard(config_manager, ready: bool):
    if not RICH_AVAILABLE:
        return False

    from rich.console import Group
    from rich.table import Table
    from rich.text import Text

    profile = config_manager.get_active_profile()
    profiles = config_manager.config.profiles
    playlist = profile.playlist_name or 'Not configured'
    context = Table.grid(expand=True)
    context.add_column(ratio=1)
    context.add_column(justify='right')
    status = Text('● ', style='anr.success' if ready else 'anr.warning')
    status.append('Ready' if ready else 'Setup required', style='anr.text')
    context.add_row(Text(profile.name, style='anr.heading'), status)
    last_check = ('Checked ' + datetime.fromtimestamp(profile.last_check).strftime('%d %b, %H:%M')
                  if profile.last_check else 'Never checked')
    context.add_row(detail_text(f'{len(profile.artists)} artists · {playlist}'),
                    Text(last_check, style='anr.secondary'))
    body = Group(
        menu_group('Releases', [('1', 'Check current profile', profile.name),
                                ('2', 'Check all profiles', f'{len(profiles)} profiles')]),
        Text(''),
        menu_group('Library', [('3', 'Artists', f'{len(profile.artists)} tracked'),
                               ('4', 'Playlist', playlist)]),
        Text(''),
        menu_group('Preferences', [('5', 'Profiles', f'{len(profiles)} profiles'),
                                   ('6', 'Settings', '')]),
        Text(''),
        menu_footer([('s', 'Schedule'), ('7', 'Import / Export'), ('q', 'Quit')]),
    )
    console.print(menu_outline(
        open_block(Text(APP_NAME, style='anr.heading'), body,
                   right=f'v{APP_VERSION}', context=context),
    ))
    return True


def display_profiles_dashboard(config_manager):
    profiles = config_manager.config.profiles
    if not RICH_AVAILABLE:
        print('\nProfiles\n')
        for index, profile in enumerate(profiles, 1):
            active = ' [Active]' if profile.id == config_manager.config.active_profile_id else ''
            print(f'  [{index}] {profile.name} · {len(profile.artists)} artists{active}')
        print('\n[n] New  [r] Rename  [d] Duplicate  [x] Delete  [0] Back\n')
        return

    from rich.console import Group
    from rich.cells import cell_len
    from rich.table import Table
    from rich.text import Text

    rows = []
    for index, profile in enumerate(profiles, 1):
        active = profile.id == config_manager.config.active_profile_id
        count = len(profile.artists)
        rows.append((f'[{index}]', profile.name, f'{count} {"artist" if count == 1 else "artists"}',
                     profile.playlist_name or 'Not configured', active))

    # Measure complete cells, including spacing and the frame, before choosing a layout.
    # Neither profile names nor playlist names should wrap inside a table row.
    widths = [max((cell_len(row[column]) for row in rows), default=0) for column in range(4)]
    status_width = 8 if any(row[4] for row in rows) else 0
    table_width = sum(widths) + status_width + 8 + 6
    width = min(console.width, max(68, table_width))
    if table_width <= console.width:
        table = Table.grid(expand=True, padding=(0, 2, 0, 0), pad_edge=False)
        for column, column_width in enumerate(widths):
            table.add_column(width=column_width, no_wrap=True,
                             justify='right' if column == 2 else 'left')
        table.add_column(width=status_width, no_wrap=True, justify='right')
        for key, name, count, playlist, active in rows:
            status = Text('● ', style='anr.accent') if active else Text('')
            if active:
                status.append('Active', style='anr.text')
            table.add_row(Text(key, style='anr.shortcut'),
                          Text(name, style='anr.heading' if active else 'anr.text'),
                          detail_text(count), Text(playlist, style='anr.secondary'), status)
        choices = table
    else:
        cards = []
        for key, name, count, playlist, active in rows:
            if cards:
                cards.append(Text(''))
            heading = Text(key + '  ', style='anr.shortcut')
            heading.append(name, style='anr.heading' if active else 'anr.text')
            if active:
                heading.append('  ●', style='anr.accent')
            cards.extend([heading, detail_text('     ' + count + (' · Active' if active else '')),
                          Text('     ' + playlist, style='anr.secondary')])
        choices = Group(*cards)
    body = Group(Text('Choose profile', style='anr.secondary'), choices, Text(''),
                 menu_footer([('n', 'New'), ('r', 'Rename'), ('d', 'Duplicate'), ('x', 'Delete')]),
                 Text(''), menu_footer([('0', 'Back')]))
    console.print(menu_outline(open_block(Text('Profiles', style='anr.heading'), body,
                                         right=f'{len(profiles)} profiles', width=width), width=width))


def display_menu(title: str, items: list, show_back: bool = True, *,
                 section_titles=(), subtitle: str = '', outlined: bool = True):
    groups = [[]]
    for item in items:
        if item is None:
            if groups[-1]:
                groups.append([])
        elif item[0]:
            groups[-1].append(item)
    groups = [rows for rows in groups if rows]
    if not RICH_AVAILABLE:
        print(f'\n{title}')
        if subtitle:
            print(subtitle)
        for index, rows in enumerate(groups):
            print('\n' + (section_titles[index] if index < len(section_titles) else ''))
            for key, label, hint in rows:
                print(f'  [{key}] {label}' + (f'  ({hint})' if hint else ''))
        if show_back:
            print('\n  [0] Back')
        print()
        return

    from rich.console import Group
    from rich.text import Text

    parts = []
    for index, rows in enumerate(groups):
        if index:
            parts.append(Text(''))
        parts.append(menu_group(section_titles[index] if index < len(section_titles) else '', rows))
    if show_back:
        parts.extend([Text(''), menu_footer([('0', 'Back')])])
    content = open_block(Text(title, style='anr.heading'), Group(*parts), subtitle=subtitle)
    console.print(menu_outline(content) if outlined else content, width=screen_width())


def display_settings(profile, notifications: bool):
    days = profile.days_to_check if profile.days_to_check > 0 else 'All'
    groups = [
        ('Checking', [('i', 'Check interval', f'{profile.check_interval}h', None),
                      ('d', 'Days to check', str(days), None),
                      ('s', 'Sort by date', 'On' if profile.sort_by_date else 'Off', profile.sort_by_date)]),
        ('Filtering', [('r', 'Skip remixes', 'On' if profile.skip_remixes else 'Off', profile.skip_remixes),
                       ('p', 'Skip low popularity', 'On' if profile.skip_low_popularity else 'Off', profile.skip_low_popularity),
                       ('m', 'Minimum popularity', str(profile.min_popularity), None),
                       ('a', 'Skip long albums', 'On' if profile.skip_long_albums else 'Off', profile.skip_long_albums),
                       ('l', 'Limit per album', 'On' if profile.limit_songs_per_album else 'Off', profile.limit_songs_per_album),
                       ('x', 'Maximum songs per album', str(profile.max_songs_per_album), None),
                       ('u', 'Skip similar tracks', 'On' if profile.skip_similar_duplicates else 'Off', profile.skip_similar_duplicates)]),
        ('Notifications', [('n', 'Spotify notifications (global)', 'On' if notifications else 'Off', notifications)]),
    ]
    release_count = len(profile.tracked_releases)
    track_count = len(profile.tracked_tracks)
    if not RICH_AVAILABLE:
        print(f'\nSettings · {profile.name}')
        for label, rows in groups:
            print(f'\n{label}')
            for key, name, value, _ in rows:
                print(f'  [{key}] {name}: {value}')
        print(f'\nTracked: {release_count} releases · {track_count} tracks')
        print('[reset] Clear history  [0] Back\n')
        return

    from rich.console import Group
    from rich.table import Table
    from rich.text import Text

    sections = []
    for index, (label, rows) in enumerate(groups):
        if index:
            sections.append(Text(''))
        table = Table.grid(expand=True, padding=(0, 2, 0, 0))
        table.add_column(width=5, style='anr.shortcut', no_wrap=True)
        table.add_column(ratio=1, style='anr.text')
        table.add_column(justify='right', width=5, no_wrap=True)
        sections.extend([Text(label, style='anr.secondary'), table])
        for key, name, value, enabled in rows:
            value_text = Text()
            if enabled is True:
                value_text.append('● ', style='anr.success')
            value_style = 'anr.secondary' if enabled is False else 'anr.text'
            if enabled is None and any(char.isdigit() for char in value):
                value_style = 'anr.heading'
            value_text.append(value, style=value_style)
            table.add_row(Text(f'[{key}]', style='anr.shortcut'), Text(name), value_text)
    tracked = detail_text(f'Tracked  {release_count} releases · {track_count} tracks')
    body = Group(*sections, Text(''), tracked, Text(''), menu_footer([('reset', 'Clear history'), ('0', 'Back')]))
    console.print(menu_outline(open_block(Text('Settings', style='anr.heading'), body,
                                         subtitle=f'{APP_NAME} / {profile.name}')))
