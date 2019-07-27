#!/usr/bin/env python
import itertools
import math
from pathlib import Path
import sys
import collections
from dataclasses import dataclass
import datetime
import enum
import shutil

import htcondor

import click


def get_events(event_log_path):
    yield from htcondor.JobEventLog(Path(event_log_path).as_posix()).events(0)


class JobStatus(enum.IntEnum):
    UNKNOWN = 0
    IDLE = 1
    RUNNING = 2
    REMOVED = 3
    COMPLETED = 4
    HELD = 5
    SUSPENDED = 6


SYMBOLS = [' ', 'I', 'R', 'X', 'C', 'H', 'S']
STATUS_TO_SYMBOL = dict(zip(JobStatus, SYMBOLS))

COLORS = ['black', 'yellow', 'blue', 'magenta', 'green', 'red', 'magenta']
SYMBOL_TO_COLOR = dict(zip(SYMBOLS, COLORS))

JOB_EVENT_STATUS_TRANSITIONS = {
    htcondor.JobEventType.SUBMIT: JobStatus.IDLE,
    htcondor.JobEventType.JOB_EVICTED: JobStatus.IDLE,
    htcondor.JobEventType.JOB_UNSUSPENDED: JobStatus.IDLE,
    htcondor.JobEventType.JOB_RELEASED: JobStatus.IDLE,
    htcondor.JobEventType.SHADOW_EXCEPTION: JobStatus.IDLE,
    htcondor.JobEventType.JOB_RECONNECT_FAILED: JobStatus.IDLE,
    htcondor.JobEventType.JOB_TERMINATED: JobStatus.COMPLETED,
    htcondor.JobEventType.EXECUTE: JobStatus.RUNNING,
    htcondor.JobEventType.JOB_HELD: JobStatus.HELD,
    htcondor.JobEventType.JOB_SUSPENDED: JobStatus.SUSPENDED,
    htcondor.JobEventType.JOB_ABORTED: JobStatus.REMOVED,
}


def main(event_log_path):
    job_states = {}
    job_state_counts = collections.Counter()
    counts_over_time = []

    for event in get_events(event_log_path):
        event_key = (event.cluster, event.proc)
        new_status = JOB_EVENT_STATUS_TRANSITIONS.get(event.type, None)

        if new_status is not None:
            old_status = job_states.get(event_key, None)

            job_states[event_key] = new_status
            job_state_counts[new_status] += 1

            if old_status is not None:
                job_state_counts[old_status] -= 1

        counts_over_time.append((event.timestamp, job_state_counts.copy()))

    # print(job_state_counts)
    # for timestamp, counts in counts_over_time:
    #     print(timestamp, counts)

    term = shutil.get_terminal_size((80, 20))

    width = term.columns - 10
    height = term.lines - 10

    hist = histogram(counts_over_time, width, height)

    rows = ['│' + row for row in hist.splitlines()]
    rows.append('└' + ('─' * (width)))

    first_time, _ = counts_over_time[0]
    last_time, _ = counts_over_time[-1]

    left_date_str = datetime.datetime.fromtimestamp(first_time).strftime('%y-%m-%d %H:%M:%S').ljust(width + 1)
    right_date_str = datetime.datetime.fromtimestamp(last_time).strftime('%y-%m-%d %H:%M:%S').rjust(width + 1)
    time_str = 'Time'.center(width + 1)
    rows.append(merge_strings(left_date_str, right_date_str, time_str))
    # rows.append(f'{left_date_str}{" " * (width - len(left_date_str) - len(right_date_str) + 1)}{right_date_str}')

    max_jobs = max(total_counts(c) for _, c in counts_over_time)

    extra_len = max(len(str(max_jobs)), len('# Jobs'))

    new_rows = []
    for idx, row in enumerate(rows):
        if idx == 0:
            new_rows.append(str(max_jobs).rjust(extra_len) + row)
        elif idx == len(rows) - 2:
            new_rows.append('0'.rjust(extra_len) + row)
        elif idx == len(rows) // 2:
            new_rows.append('# Jobs'.rjust(extra_len) + row)
        else:
            new_rows.append((' ' * extra_len) + row)

    rows = new_rows

    hist = '\n'.join(rows)

    click.echo(hist)


def merge_strings(*strings):
    max_len = max(len(s) for s in strings)

    out = [' '] * max_len

    for string in strings:
        for idx, char in enumerate(string):
            if out[idx] == ' ' and char != ' ':
                out[idx] = char

    return ''.join(out)


def histogram(counts_over_time, width, height):
    first_time, _ = counts_over_time[0]
    last_time, last_counts = counts_over_time[-1]

    counts = [avg_counts(group) for group in group_counts_by_time(counts_over_time, width)]
    counts[-1] = last_counts

    max_jobs = max(total_counts(c) for c in counts if c is not None)
    columns = []
    for count in counts:
        if count is None:
            columns.append(columns[-1])
            continue

        bar_lens = calculate_column_partition(count, max_jobs, height)
        columns.append(''.join(symbol * bar_lens[status] for status, symbol in STATUS_TO_SYMBOL.items()))

    rows = list(reversed(list(map(list, itertools.zip_longest(*columns, fillvalue = ' ')))))
    rows = [''.join(click.style('█' * len(list(group)), fg = SYMBOL_TO_COLOR[symbol]) for symbol, group in itertools.groupby(row)) for row in rows]

    return '\n'.join(rows)


def calculate_column_partition(counts, max_jobs, height):
    raw_split = [(counts.get(status, 0) / max_jobs) * height for status in JobStatus]

    int_split = [None] * len(raw_split)
    for idx, entry in enumerate(raw_split):
        dec = entry - math.floor(entry)

        if entry == 0:
            int_split[idx] = 0
        elif math.floor(entry) == 0:
            int_split[idx] = 1
            continue
        elif dec <= 0.5:
            int_split[idx] = math.floor(entry)
        elif dec > 0.5:
            int_split[idx] = math.ceil(entry)

    return {k: v for k, v in zip(JobStatus, int_split)}


def _calculate_bar_component_len(count, total, bar_width):
    if count == 0:
        return 0

    return max(int((count / total) * bar_width), 1)


def total_counts(counter):
    return sum(counter.values())


def group_counts_by_time(counts_over_time, n_divisions):
    first_time, _ = counts_over_time[0]
    last_time, _ = counts_over_time[-1]

    dt = (last_time - first_time) / n_divisions

    left_idx = 0
    right_idx = 0
    for left_time in (first_time + (n * dt) for n in range(n_divisions)):
        right_time = left_time + dt

        for right_idx, (timestamp, _) in enumerate(counts_over_time[left_idx:], start = left_idx):
            if timestamp > right_time:
                break

        yield counts_over_time[left_idx: right_idx]
        left_idx = right_idx


def avg_counts(counts_over_time):
    lc = len(counts_over_time)
    if lc == 0:
        return None

    counts = [counts for _, counts in counts_over_time]

    return collections.Counter({k: round(v / lc) for k, v in sum(counts, collections.Counter()).items()})


if __name__ == '__main__':
    main(sys.argv[1])
