"""Explicit demo presets; risk caps and broker guards remain independent."""
PROFILES = {
    'conservative': {},
    'fast-demo': dict(strategy='scalp-m1', risk_percent=.1, cooldown_seconds=30,
                      max_hold_seconds=180, max_trades_day=30, max_consecutive_losses=3,
                      session_start_utc=6, session_end_utc=20, news_mode='automatic'),
}

def apply_profile(config, name):
    if name not in PROFILES:
        raise ValueError('پروفایل نامعتبر است')
    return dict(config, **PROFILES[name], execution_profile=name)
