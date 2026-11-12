"""Public widgets used by the two IranRebate pages requested by the user.

Embed route and config fields were verified against FinanceKit's public
widget-loader.js on 2026-10-30. No account data is passed to these widgets.
This is a display integration, not a machine-readable trading-news feed.
"""
import base64
import json

WIDGET_ORIGIN = 'https://widget.financekit.org'
SOURCES = {
    'forex-sessions': ('سشن‌های فارکس', 'https://iranrebate.com/trading-tools/forex-sessions/', 680),
    'economic-calendar': ('تقویم اقتصادی فارسی', 'https://iranrebate.com/trading-tools/forex-factory/', 840),
}


def widget_url(kind):
    if kind not in SOURCES:
        raise ValueError('Unsupported public widget')
    settings = dict(fontFamily='Peyda',primaryColor='#d7b676',backgroundColor='#111821',
                    fontColor='#edf0f4',colorScheme='dark',locale='fa',spacing=14,
                    borderRadius=14,autoSync=True)
    widget_config = dict(timezone='Asia/Tehran',timezonePickerEnabled=True)
    if kind == 'forex-sessions':
        widget_config['newsImpacts'] = ['high']
        settings['widgetHeaders'] = {kind:dict(visible=False,title='',subtitle='')}
    config = dict(settings=settings,dimensions=dict(width=1140,height=SOURCES[kind][2]),
                  fluidWidth=True,previewDevice='desktop',planMode='free',locale='fa',
                  colorScheme='dark',widgetConfig=widget_config)
    encoded = base64.urlsafe_b64encode(json.dumps(config,separators=(',',':')).encode()).decode().rstrip('=')
    return f'{WIDGET_ORIGIN}/embed/{kind}?c={encoded}'


def market_tools():
    return dict(timezone='Asia/Tehran',provider='FinanceKit',news_review_required=True,
                calendar=dict(provider='Forex Factory · Fair Economy',endpoint='/api/calendar',review_required=True),
                widgets=[dict(key=kind,title=title,source_url=url,embed_url=widget_url(kind),height=height)
                         for kind,(title,url,height) in SOURCES.items()])
