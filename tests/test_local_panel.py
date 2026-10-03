import http.client
import json
import os
from pathlib import Path
import threading
import unittest
from local_panel.server import App,Server,Handler
from local_panel.manager import Manager

class LocalPanelTests(unittest.TestCase):
    def test_local_http_authorization(self):
        server=Server(('127.0.0.1',0),Handler);server.address='127.0.0.1:'+str(server.server_port);server.token='local-test-token';server.app=App()
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def call(method,path,headers={},body=None):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            conn.request(method,path,body=body,headers=headers);response=conn.getresponse();raw=response.read();conn.close();return response.status,raw
        try:
            self.assertEqual(call('GET','/')[0],200)
            self.assertEqual(call('GET','/api/state')[0],403)
            self.assertEqual(call('GET','/api/state',{'X-Genesis-Token':server.token})[0],200)
            self.assertEqual(call('GET','/',{'Host':'evil.example'})[0],403)
            headers={'X-Genesis-Token':server.token,'Content-Type':'application/json','Origin':'http://evil.example'}
            self.assertEqual(call('POST','/api/action',headers,json.dumps({'action':'stop'}))[0],403)
            headers['Origin']='http://'+server.address
            self.assertEqual(call('POST','/api/action',headers,json.dumps({'action':'unknown'}))[0],400)
            self.assertEqual(call('GET','/../../manager.py')[0],404)
        finally:server.shutdown();server.server_close();thread.join()
    def test_operator_input_bounds(self):
        manager=Manager()
        for values in ([],[1,2,3],[1,2,3,float('nan')],[1,2,3,float('inf')],[1,2,3,1e41],[1]*65):
            with self.assertRaises(ValueError):manager.start(values)
        self.assertEqual(manager.processes,[])
    def test_full_real_local_lifecycle(self):
        manager=Manager()
        try:
            manager.start([10,20,30,40]);manager.test()
            self.assertEqual(manager.cursor(),4)
            self.assertEqual(manager.results[-1]['result']['mean'],25)
            self.assertEqual(manager.quality['mae'],20)
            self.assertAlmostEqual(manager.quality['rmse'],425**.5)
            self.assertTrue(any('E2E PASS' in e['message'] for e in manager.events))
            temp=manager.temp.name
        finally:manager.stop()
        self.assertFalse(Path(temp).exists())
