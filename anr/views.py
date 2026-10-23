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
    status = Text('● ', style='anr.secondary' if ready and not profile.enabled else 'anr.success' if ready else 'anr.warning')
    status.append(('Ready' if profile.enabled else 'Manual only') if ready else 'Setup required', style='anr.text')
    context.add_row(Text(profile.name, style='anr.heading'), status)
    last_check = ('Checked ' + datetime.fromtimestamp(profile.last_check).strftime('%d %b, %H:%M')
                  if profile.last_check else 'Never checked')
    context.add_row(detail_text(f'{len(profile.artists)} artists · {playlist}'),
                    Text(last_check, style='anr.secondary'))
    body = Group(
        menu_group('Releases', [('1', 'Check current profile', profile.name),
                                ('2', 'Check all profiles', f'{sum(p.enabled for p in profiles)} enabled')]),
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
    active_profile = config_manager.get_active_profile()
    enabled_action = 'Disable' if active_profile.enabled else 'Enable'
    if not RICH_AVAILABLE:
        print('\nProfiles\n')
        for index, profile in enumerate(profiles, 1):
            active = ' [Active]' if profile.id == config_manager.config.active_profile_id else ''
            status = 'Enabled' if profile.enabled else 'Disabled'
            print(f'  [{index}] {profile.name} · {len(profile.artists)} artists · {status}{active}')
        print(f'\nActions for active profile: {active_profile.name}')
        print(f'[r] Rename  [d] Duplicate  [x] Delete  [e] {enabled_action}')
        print('\n[n] New profile  [0] Back\n')
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
                     profile.playlist_name or 'Not configured', active,
                     ('Active · ' if active else '') + ('Enabled' if profile.enabled else 'Disabled')))

    # Measure complete cells, including spacing and the frame, before choosing a layout.
    # Neither profile names nor playlist names should wrap inside a table row.
    widths = [max((cell_len(row[column]) for row in rows), default=0) for column in range(4)]
    status_width = max((cell_len(row[5]) for row in rows), default=0)
    table_width = sum(widths) + status_width + 8 + 6
    width = min(console.width, max(68, table_width))
    if table_width <= console.width:
        table = Table.grid(expand=True, padding=(0, 2, 0, 0), pad_edge=False)
        for column, column_width in enumerate(widths):
            table.add_column(width=column_width, no_wrap=True,
                             justify='right' if column == 2 else 'left')
        table.add_column(width=status_width, no_wrap=True, justify='right')
        for key, name, count, playlist, active, scan_status in rows:
            status = Text(scan_status, style='anr.secondary' if scan_status.endswith('Disabled') else 'anr.text')
            table.add_row(Text(key, style='anr.shortcut'),
                          Text(name, style='anr.heading' if active else 'anr.text'),
                          detail_text(count), Text(playlist, style='anr.secondary'), status)
        choices = table
    else:
        cards = []
        for key, name, count, playlist, active, scan_status in rows:
            if cards:
                cards.append(Text(''))
            heading = Text(key + '  ', style='anr.shortcut')
            heading.append(name, style='anr.heading' if active else 'anr.text')
            if active:
                heading.append('  ●', style='anr.accent')
            cards.extend([heading, detail_text('     ' + count + ' · ' + scan_status),
                          Text('     ' + playlist, style='anr.secondary')])
        choices = Group(*cards)
    body = Group(Text('Switch profile', style='anr.secondary'), choices, Text(''),
                 Text('Actions for active profile: ' + active_profile.name, style='anr.secondary'),
                 menu_footer([('r', 'Rename'), ('d', 'Duplicate'), ('x', 'Delete'), ('e', enabled_action)]),
                 Text(''), menu_footer([('n', 'New profile'), ('0', 'Back')]))
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


def _settings_rows(rows):
    """Keep setting values visible even when a terminal needs stacked rows."""
    from rich.console import Group
    from rich.table import Table
    from rich.text import Text

    if console.width < 64:
        lines = []
        for key, label, value in rows:
            line = Text(f'[{key}]  ', style='anr.shortcut')
            line.append(label, style='anr.text')
            lines.append(line)
            if value:
                lines.append(Text('     ' + value, style='anr.secondary'))
        return Group(*lines)
    table = Table.grid(expand=True, padding=(0, 2, 0, 0), pad_edge=False)
    table.add_column(width=5, no_wrap=True)
    table.add_column(ratio=1)
    table.add_column(justify='right', no_wrap=True)
    for key, label, value in rows:
        table.add_row(Text(f'[{key}]', style='anr.shortcut'), Text(label),
                      Text(value, style='anr.text'))
    return table


def display_settings(profile, notifications: bool):
    from types import SimpleNamespace
    from .settings import SETTINGS, SETTING_GROUPS

    config = SimpleNamespace(spotify_notifications=notifications)
    groups = [(group, [(s.key, s.label, s.format(s.current(profile, config)))
                       for s in SETTINGS if s.group == group]) for group in SETTING_GROUPS]
    groups[-1][1].append(('h', 'Tracking history', 'View / clear'))
    if not RICH_AVAILABLE:
        print(f'\nSettings · {profile.name}')
        for label, rows in groups:
            print(f'\n{label}')
            for key, name, value in rows:
                print(f'  [{key}] {name}: {value}')
        print('\n[0] Back\n')
        return

    from rich.console import Group
    from rich.text import Text

    sections = []
    for index, (label, rows) in enumerate(groups):
        if index:
            sections.append(Text(''))
        sections.extend([Text(label, style='anr.secondary'), _settings_rows(rows)])
    body = Group(*sections, Text(''), menu_footer([('0', 'Back')]))
    console.print(menu_outline(open_block(Text('Settings', style='anr.heading'), body,
        subtitle='Profile settings · changes apply immediately', right=profile.name)))


def _settings_detail(title, profile_name, scope, paragraphs, rows=(), *, back_key='0'):
    if not RICH_AVAILABLE:
        print(f'\n{title} · {profile_name}\n{scope}\n')
        for message, _style in paragraphs:
            print(message + '\n')
        for key, label, value in rows:
            print(f'[{key}] {label}' + (f' · {value}' if value else ''))
        print(f'\n[{back_key}] Back\n')
        return
    from rich.console import Group
    from rich.text import Text

    parts = []
    for message, style in paragraphs:
        parts.extend([Text(message, style=style), Text('')])
    if rows:
        parts.extend([_settings_rows(rows), Text('')])
    parts.append(menu_footer([(back_key, 'Back')]))
    console.print(menu_outline(open_block(Text(title, style='anr.heading'), Group(*parts),
                                         subtitle=scope, right=profile_name)))


def display_setting_editor(setting, profile, config, *, custom=False):
    current = setting.current(profile, config)
    paragraphs = [('Current · ' + setting.format(current), 'anr.text'),
                  (setting.description, 'anr.secondary')]
    if setting.group == 'Filters':
        paragraphs.append(('Previously handled releases stay in history. Clear tracking history '
                           'to reconsider them within the release window.', 'anr.secondary'))
    if custom:
        paragraphs.append((setting.custom_help, 'anr.text'))
        rows = []
    else:
        rows = [(str(index), label, '✓ Current' if value == current else '')
                for index, (value, label) in enumerate(setting.options, 1)]
        if setting.minimum is not None:
            rows.append(('c', 'Enter a number…', ''))
    scope = 'All profiles · global preference' if setting.global_setting else 'Settings / ' + setting.group
    _settings_detail(setting.label, profile.name, scope, paragraphs, rows, back_key='b' if custom else '0')


def display_tracking_history(profile, *, confirm=False):
    releases, tracks, pending = len(profile.tracked_releases), len(profile.tracked_tracks), len(profile.pending_artists)
    counts = f'{releases:,} remembered releases · {tracks:,} remembered songs · {pending:,} pending artists'
    if confirm:
        paragraphs = [(f'Clear tracking history for {profile.name}?', 'anr.warning'),
                      (counts, 'anr.text'),
                      ('Forget remembered releases, songs and pending artist recovery. '
                       'Songs in your Spotify playlist stay there. The next check re-scans '
                       'within your release window; your filters still apply.', 'anr.secondary'),
                      ('Type CLEAR to confirm. Enter cancels.', 'anr.warning')]
        rows = []
    else:
        paragraphs = [(counts, 'anr.text'),
                      ('ANR remembers releases and songs it has already handled. Clearing '
                       'history lets a future check reconsider them within your release window. '
                       'Other filters still apply.', 'anr.secondary')]
        rows = [('c', 'Clear tracking history', '')]
    _settings_detail('Clear tracking history' if confirm else 'Tracking history',
                     profile.name, 'Settings / History', paragraphs, rows)
