"""Compare broker fills to same-terminal ticks; discrepancy is a review flag."""
def audit_fill(deal, ticks, spec):
    rows = [t for t in ticks if t['bid'] > 0 and t['ask'] >= t['bid']
            and abs(t['time_msc']-deal['time_msc']) <= 1000]
    if not rows:
        return dict(status='unavailable',reason='تیک هم‌زمان برای تطبیق اجرای معامله موجود نیست',deal=deal['ticket'])
    # Prefer the latest quote before execution; never use a later quote if one exists.
    before = [t for t in rows if t['time_msc'] <= deal['time_msc']]
    tick = max(before,key=lambda t:t['time_msc']) if before else min(rows,key=lambda t:t['time_msc'])
    reference = tick['ask'] if deal['type']==0 else tick['bid']
    tolerance = max(3*(tick['ask']-tick['bid']),20*spec.get('point',0),5*spec.get('tick_size',0),1e-8)
    difference = deal['price']-reference
    mismatch = abs(difference)>tolerance
    return dict(status='quote_mismatch' if mismatch else 'consistent',
        reason='قیمت اجرای بروکر با تیک هم‌زمان مغایرت دارد؛ نیازمند بررسی' if mismatch else 'مغایرت بزرگ در تیک موجود دیده نشد',
        deal=deal['ticket'],fill=deal['price'],bid=tick['bid'],ask=tick['ask'],
        tick_time_msc=int(tick['time_msc']),deal_time_msc=deal['time_msc'],
        difference=round(difference,8),tolerance=round(tolerance,8))
