"""MT5 standard intraday timeframes supported by the local dashboard."""
TIMEFRAMES = {'M1':60, 'M2':120, 'M3':180, 'M4':240, 'M5':300, 'M6':360,
              'M10':600, 'M12':720, 'M15':900, 'M20':1200, 'M30':1800, 'H1':3600}


def price_bar(rate, clock):
    from .clock import broker_to_utc
    return dict(time=broker_to_utc(int(rate['time']),clock), open=float(rate['open']),
                high=float(rate['high']), low=float(rate['low']), close=float(rate['close']),
                volume=float(rate['tick_volume']))
