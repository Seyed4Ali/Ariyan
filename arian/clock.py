"""Explicit broker clock conversion. Never infer an offset from a stale tick.

FxPro profile uses EU DST; observed UTC+2 on 2026-10-30. Different brokers
must select the UTC profile or an explicitly verified fixed offset.
"""
from datetime import datetime, timezone, timedelta
import calendar


def broker_offset(utc_timestamp, profile):
    mode = profile.get('mode', 'utc')
    if mode == 'utc':
        return 0
    if mode == 'fixed':
        offset = int(profile['offset_seconds'])
        if offset % 900 or abs(offset) > 14 * 3600:
            raise ValueError('Invalid verified broker offset')
        return offset
    if mode != 'fxpro-eu':
        raise ValueError('Unknown broker clock profile')
    dt = datetime.fromtimestamp(utc_timestamp, timezone.utc)
    def transition(month):
        last = calendar.monthrange(dt.year, month)[1]
        day = datetime(dt.year, month, last, 1, tzinfo=timezone.utc)
        return (day - timedelta(days=(day.weekday() + 1) % 7)).timestamp()
    return 10800 if transition(3) <= utc_timestamp < transition(10) else 7200


def broker_to_utc(server_timestamp, profile):
    guess = server_timestamp - broker_offset(server_timestamp, profile)
    return server_timestamp - broker_offset(guess, profile)
