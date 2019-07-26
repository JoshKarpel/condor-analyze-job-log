#!/usr/bin/env python

from pathlib import Path
import sys

import htcondor


def get_events(event_log_path):
    return htcondor.JobEventLog(Path(event_log_path).as_posix()).events(0)


def process_events(events):
    for event in events:
        print(event.type)


def main(event_log_path):
    process_events(get_events(event_log_path))


if __name__ == '__main__':
    main(sys.argv[1])
