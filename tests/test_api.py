import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import base64
from urllib.parse import urlsplit,parse_qs
from pathlib import Path
from http.server import ThreadingHTTPServer
from server import make_handler


class FakeEngine:
    def snapshot(self):return {'ok':True,'action':getattr(self,'last_action',None)}
    def action(self,action,payload):
        self.last_action=action
        return {'ok':True,'action':action}


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(FakeEngine(),'test-token',0))
        cls.port=cls.server.server_port
        cls.server.RequestHandlerClass=make_handler(FakeEngine(),'test-token',cls.port)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def request(self,headers):
        return urllib.request.urlopen(urllib.request.Request(self.url+'/api/control',data=b'{"action":"pause"}',
            headers={'Content-Type':'application/json',**headers}),timeout=3)

    def test_same_origin_and_token_required(self):
        with self.assertRaises(urllib.error.HTTPError) as err:self.request({'Origin':self.url})
        self.assertEqual(err.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as err:self.request({'Origin':'https://example.com','X-Arian-Token':'test-token'})
        self.assertEqual(err.exception.code,403)
        with self.request({'Origin':self.url,'X-Arian-Token':'test-token'}) as response:
            self.assertTrue(json.load(response)['ok'])

    def test_control_invalidates_state_cache_immediately(self):
        with urllib.request.urlopen(self.url+'/api/state',timeout=3) as response:json.load(response)
        with self.request({'Origin':self.url,'X-Arian-Token':'test-token'}):pass
        with urllib.request.urlopen(self.url+'/api/state',timeout=3) as response:
            self.assertEqual(json.load(response)['action'],'pause')

    def test_host_rebinding_blocked(self):
        req=urllib.request.Request(self.url+'/api/session',headers={'Host':'evil.example'})
        with self.assertRaises(urllib.error.HTTPError) as err:urllib.request.urlopen(req,timeout=3)
        self.assertEqual(err.exception.code,403)

    def test_market_tools_only_expose_public_widget_configuration(self):
        with urllib.request.urlopen(self.url+'/api/market-tools',timeout=3) as response:
            config=json.load(response)
        self.assertTrue(config['news_review_required'])
        self.assertEqual(len(config['widgets']),2)
        for widget in config['widgets']:
            url=urlsplit(widget['embed_url'])
            self.assertEqual(url.scheme+'://'+url.netloc,'https://widget.financekit.org')
            self.assertIn(url.path,('/embed/forex-sessions','/embed/economic-calendar'))
            encoded=parse_qs(url.query)['c'][0]
            options=json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
            self.assertEqual(options['widgetConfig']['timezone'],'Asia/Tehran')
            self.assertEqual(options['locale'],'fa')
            self.assertNotIn('account',options)

    def test_external_widgets_cannot_load_parent_scripts_or_connect_to_api(self):
        with urllib.request.urlopen(self.url+'/',timeout=3) as response:
            policy=response.headers['Content-Security-Policy']
        directives=dict((part.strip().split(' ',1)+[''])[:2] for part in policy.split(';') if part.strip())
        self.assertEqual(directives['frame-src'],'https://widget.financekit.org')
        self.assertEqual(directives['script-src'],"'self'")
        self.assertEqual(directives['connect-src'],"'self'")
        self.assertEqual(directives['frame-ancestors'],"'none'")


if __name__=='__main__':unittest.main()
