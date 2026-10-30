import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
from server import make_handler


class FakeEngine:
    def snapshot(self):return {'ok':True}
    def action(self,action,payload):return {'ok':True,'action':action}


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

    def test_host_rebinding_blocked(self):
        req=urllib.request.Request(self.url+'/api/session',headers={'Host':'evil.example'})
        with self.assertRaises(urllib.error.HTTPError) as err:urllib.request.urlopen(req,timeout=3)
        self.assertEqual(err.exception.code,403)


if __name__=='__main__':unittest.main()
