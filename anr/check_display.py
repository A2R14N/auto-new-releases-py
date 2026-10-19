"""One live overview for a sequential run of profile checks."""

from threading import RLock

from .constants import RICH_AVAILABLE, console as default_console


class CheckAllDisplay:
    PHASES = {
        'init': ('Preparing', 'Reading playlist', 'anr.accent'),
        'artist': ('Scanning', 'Scanning releases', 'anr.accent'),
        'artist_done': ('Scanning', 'Scanning releases', 'anr.accent'),
        'retry_wait': ('Retrying', 'Waiting to retry failed artists', 'anr.warning'),
        'retry': ('Retrying', 'Retrying failed artist', 'anr.warning'),
        'retry_done': ('Retrying', 'Recovering failed artists', 'anr.warning'),
        'adding': ('Adding', 'Adding tracks', 'anr.accent'),
        'added': ('Finalizing', 'Track additions confirmed', 'anr.accent'),
        'sorting': ('Sorting', 'Sorting playlist', 'anr.accent'),
        'done': ('Finalizing', 'Finishing profile', 'anr.accent'),
    }

    def __init__(self, profiles, *, console=None, enabled=True, dry_run=False, interactive=False):
        self.profiles = list(profiles)
        self.console = console if console is not None else default_console
        self.enabled = enabled
        self.dry_run = dry_run
        self.interactive = interactive
        self.live_mode = bool(enabled and RICH_AVAILABLE and self.console.is_terminal)
        self.results = [None] * len(self.profiles)
        self.current = None
        self.phase = 'init'
        self.artist_name = ''
        self.artist_index = self.scan_total = self.scanned = 0
        self.checked = self.pending = self.current_added = self.tracks_found = 0
        self.finished = self.cancelled = self.interrupted = False
        self._live = None
        self._lock = RLock()

    @property
    def total_added(self):
        total = sum(result.total_tracks_added for result in self.results if result is not None)
        return total + (self.current_added if self.current is not None and self.results[self.current] is None else 0)

    @property
    def total_pending(self):
        total = sum(result.artists_pending for result in self.results if result is not None)
        return total + (self.pending if self.current is not None and self.results[self.current] is None else 0)

    def _emit(self, message, style='anr.secondary'):
        if RICH_AVAILABLE:
            from rich.text import Text
            self.console.print(Text(message, style=style))
        else:
            print(message)

    def __enter__(self):
        if self.live_mode:
            from rich.live import Live
            self._live = Live(self, console=self.console, refresh_per_second=8,
                              vertical_overflow='crop')
            self._live.start(refresh=True)
        elif self.enabled:
            self._emit(f'Checking {len(self.profiles)} profiles')
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type:
            with self._lock:
                self.cancelled = issubclass(exc_type, KeyboardInterrupt)
                self.interrupted = not self.cancelled
        if self._live:
            self._live.refresh()
            self._live.stop()
            self._live = None
        return False

    def start_profile(self, index):
        with self._lock:
            self.current = index
            self.phase = 'init'
            self.artist_name = ''
            self.artist_index = self.scan_total = self.scanned = 0
            self.checked = self.pending = self.current_added = self.tracks_found = 0
        if self._live:
            self._live.refresh()
        elif self.enabled and not self.live_mode:
            self._emit(f'[{index + 1}/{len(self.profiles)}] {self.profiles[index].name}', 'anr.heading')

    def update(self, event):
        with self._lock:
            previous = self.phase
            self.phase = event.phase
            if event.phase == 'artist':
                self.artist_name = event.artist_name
                self.artist_index = event.artist_current
                self.scan_total = event.artist_total
                self.scanned = max(0, event.artist_current - 1)
            elif event.phase == 'artist_done':
                self.scan_total = event.artist_total
                self.scanned = event.artist_current
            elif event.phase == 'retry':
                self.artist_name = event.artist_name
            if event.phase in ('artist_done', 'retry_wait', 'retry', 'retry_done', 'done'):
                self.checked = event.artists_checked
                self.pending = event.artists_pending
            if event.phase in ('added', 'done'):
                self.current_added = event.tracks_added
            if event.phase == 'adding':
                self.tracks_found = event.tracks_found
        if self._live and previous != event.phase and event.phase not in ('artist', 'artist_done'):
            self._live.refresh()
        elif self.enabled and not self.live_mode:
            if event.phase == 'artist':
                self._emit(f'  [{event.artist_current}/{event.artist_total}] Checking {event.artist_name}')
            elif event.phase in ('init', 'retry_wait', 'retry', 'adding', 'sorting'):
                self._emit('  ' + (event.message or self.PHASES[event.phase][1]))

    def complete_profile(self, result):
        with self._lock:
            self.results[self.current] = result
            self.current = None
        if self._live:
            self._live.refresh()
        elif self.enabled and not self.live_mode:
            status = result.status.value
            if status == 'skipped':
                self._emit('  Skipped: ' + result.error_message, 'anr.warning')
            elif self.dry_run:
                self._emit(f'  Would add {result.total_tracks_added} tracks')
                if status in ('error', 'partial'):
                    self._emit('  ' + result.error_message, 'anr.error' if status == 'error' else 'anr.warning')
            elif status in ('error', 'partial'):
                self._emit('  ' + result.summary(), 'anr.error' if status == 'error' else 'anr.warning')
            else:
                self._emit('  ' + result.summary())

    def finish(self):
        with self._lock:
            self.finished = True
        if self._live:
            self._live.refresh()
        elif self.enabled:
            label = 'Would add' if self.dry_run else 'Added'
            self._emit(f'{label} {self.total_added} tracks · {self.total_pending} artists pending', 'anr.heading')

    def report_issues(self):
        # Keep full diagnostic details available without filling the live panel.
        if self.enabled and self.live_mode:
            for result in self.results:
                if result is not None and result.status.value in ('error', 'partial', 'skipped') and result.error_message:
                    self._emit(f'{result.profile_name}: {result.error_message}', 'anr.warning')

    def _row_status(self, index, compact):
        result = self.results[index]
        if result is not None:
            status = result.status.value
            if status == 'skipped':
                return '–', 'Skipped', 'anr.secondary'
            if status == 'error':
                return '✗', 'Error', 'anr.error'
            if status == 'partial':
                label = f'{result.artists_pending} pending' if result.artists_pending else 'Incomplete'
                return '⚠', label, 'anr.warning'
            if result.total_tracks_added:
                suffix = 'planned' if self.dry_run else 'added'
                return '✓', f'{result.total_tracks_added} {suffix}', 'anr.success'
            return '✓', 'Done' if compact else 'Up to date', 'anr.success'
        if index == self.current:
            if self.cancelled or self.interrupted:
                return '–', 'Cancelled' if self.cancelled else 'Interrupted', 'anr.warning'
            label, _, style = self.PHASES.get(self.phase, self.PHASES['init'])
            if compact:
                label = {'Preparing': 'Read', 'Scanning': 'Scan', 'Retrying': 'Retry',
                         'Finalizing': 'Finish', 'Sorting': 'Sort'}.get(label, label)
            return ('↻' if 'retry' in self.phase else '›'), label, style
        return '○', 'Not checked' if self.cancelled or self.interrupted else 'Waiting', 'anr.secondary'

    def __rich__(self):
        with self._lock:
            return self.render()

    def render(self):
        from rich import box
        from rich.console import Group
        from rich.panel import Panel
        from rich.progress_bar import ProgressBar
        from rich.rule import Rule
        from rich.table import Table
        from rich.text import Text

        width = min(68, self.console.width)
        compact = width < 42
        tight = self.console.height < 34
        complete = sum(result is not None for result in self.results)
        count = len(self.profiles)
        header = Table.grid(expand=True)
        header.add_column(ratio=1)
        header.add_column(justify='right')
        position = f'{self.current + 1}/{count}' if self.current is not None else f'{complete}/{count}'
        header.add_row(Text('Check all' if compact else 'Check all profiles', style='anr.heading'),
                       Text(position if compact else ('Finished ' if self.finished else 'Profile ') + position))
        waiting = sum(result is None and index != self.current for index, result in enumerate(self.results))
        overview = f'{complete} finished'
        if not self.finished:
            overview += f' · {waiting} waiting' if compact else f' · {int(self.current is not None)} active · {waiting} waiting'
        if self.dry_run:
            overview += ' · Dry run'
        parts = [header, Text(overview, style='anr.secondary'), Text('')]
        table = Table.grid(expand=True, padding=(0, 1, 0, 0), pad_edge=False)
        table.add_column(width=2, no_wrap=True)
        table.add_column(ratio=1, no_wrap=True)
        table.add_column(justify='right', no_wrap=True)
        # Leave room for the current activity when there are many profiles or few rows.
        limit = max(3, self.console.height - (19 if tight else 26))
        anchor = self.current if self.current is not None else max(0, complete - 1)
        start = max(0, min(anchor - limit // 2, count - limit))
        stop = min(count, start + limit)
        if start:
            table.add_row('', Text(f'… {start} earlier', style='anr.secondary'), '')
        for index in range(start, stop):
            symbol, label, style = self._row_status(index, compact)
            table.add_row(Text(symbol, style=style),
                          Text(self.profiles[index].name, style='anr.heading' if index == self.current else 'anr.text'),
                          Text(label, style=style if index == self.current or style in ('anr.warning', 'anr.error') else 'anr.secondary'))
        if stop < count:
            table.add_row('', Text(f'… {count - stop} more', style='anr.secondary'), '')
        parts.extend([table, Rule(style='anr.rule')])
        if not tight:
            parts.append(Text(''))
        issues = [r for r in self.results if r is not None and r.status.value in ('error', 'partial', 'skipped')]
        if self.cancelled or self.interrupted:
            parts.append(Text('Check cancelled' if self.cancelled else 'Check interrupted', style='anr.warning'))
        elif self.finished:
            parts.append(Text('Check finished with issues' if issues else 'Check complete',
                              style='anr.warning' if issues else 'anr.heading'))
            parts.append(Text(f'{complete} profiles finished · {self.total_pending} artists pending', style='anr.secondary'))
        elif self.current is not None:
            parts.append(Text(self.profiles[self.current].name, style='anr.heading'))
            _, label, style = self.PHASES.get(self.phase, self.PHASES['init'])
            parts.append(Text(label, style=style))
            if self.phase in ('artist', 'artist_done'):
                parts.extend([Text('Checking ' + self.artist_name),
                              Text(f'Artist {self.artist_index} of {self.scan_total}', style='anr.secondary')])
                bar = Table.grid(expand=True)
                bar.add_column()
                bar.add_row(ProgressBar(total=max(1, self.scan_total), completed=self.scanned,
                                       style='anr.rule', complete_style='anr.accent', finished_style='anr.accent'))
                percent = int(self.scanned * 100 / max(1, self.scan_total))
                parts.extend([bar, Text(f'{self.scanned}/{self.scan_total} scanned · {percent}%', style='anr.secondary')])
            elif self.phase.startswith('retry'):
                if self.artist_name:
                    parts.append(Text(self.artist_name))
                parts.append(Text(f'{self.checked} checked · {self.pending} pending', style='anr.secondary'))
            elif self.phase == 'adding':
                parts.extend([Text(f'Sending {self.tracks_found} tracks to Spotify…'),
                              Text('Waiting for confirmation', style='anr.secondary')])
            elif self.phase == 'sorting':
                parts.append(Text('Ordering tracks by release date…'))
            elif self.phase == 'init':
                parts.append(Text('Fetching existing playlist tracks…', style='anr.secondary'))
        else:
            parts.append(Text('Preparing next profile…', style='anr.secondary'))
        if not tight:
            parts.append(Text(''))
        parts.append(Rule(style='anr.rule'))
        verb = 'Would add' if self.dry_run else 'Added'
        parts.append(Text(f'{verb} {self.total_added} · Pending {self.total_pending}', style='anr.text'))
        if not self.finished and not self.cancelled and not self.interrupted:
            footer = Text('[Ctrl+C]', style='anr.shortcut')
            footer.append(' Cancel', style='anr.secondary')
            parts.append(footer)
        elif self.interactive:
            footer = Text('[Enter]', style='anr.shortcut')
            footer.append(' Back', style='anr.secondary')
            parts.append(footer)
        return Panel(Group(*parts), box=box.ROUNDED, border_style='anr.rule',
                     padding=(0 if tight else 1, 2), width=width)
