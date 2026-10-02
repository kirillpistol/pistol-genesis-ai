import json
import threading
import unittest
import urllib.request
import urllib.error
from level1_core.runtime import Core
from level1_core.server import Server

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.server=Server(('127.0.0.1',0),Core(),dev=True)
        self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
    def post_raw(self,path,raw):
        request=urllib.request.Request(self.url+path,data=raw,headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=3) as response:return json.load(response)
    def test_legacy_route_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:self.post_raw('/tasks',b'{}')
        self.assertEqual(error.exception.code,404)
    def test_bad_messages_and_limit(self):
        for raw in (b'{"api_version":"1.0","name":"mock","name":"bad"}',b'{"api_version":"2.0","name":"mock"}',b'{"api_version":"1.0","name":"mock","code":"exec"}'):
            with self.assertRaises(urllib.error.HTTPError) as error:self.post_raw('/v1/algorithms/attach',raw)
            self.assertEqual(error.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError) as error:self.post_raw('/v1/tasks',b'x'*65537)
        self.assertEqual(error.exception.code,413)
        self.assertEqual(self.server.core.algorithms,{})
    def test_plain_requires_explicit_local_mode(self):
        with self.assertRaises(ValueError):Server(('127.0.0.1',0),Core())
        with self.assertRaises(ValueError):Server(('0.0.0.0',0),Core(),dev=True)
