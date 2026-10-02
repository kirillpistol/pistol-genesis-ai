"""Actual HTTP demo; external numeric module required on PYTHONPATH."""
import json
import threading
import urllib.request
from level1_core.server import Server
from level1_core.runtime import Core
from level2_algorithms.numeric import REGISTRY

def main():
    servers=[Server(('127.0.0.1',0),Core(REGISTRY),dev=True) for _ in range(2)]
    threads=[threading.Thread(target=s.serve_forever) for s in servers]
    for thread in threads:thread.start()
    def call(i,path,body=None):
        request=urllib.request.Request(f'http://127.0.0.1:{servers[i].server_port}/v1/'+path,
            data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=3) as response:return json.load(response)
    try:
        for name in ('numeric','evaluator'):call(0,'algorithms/attach',{'api_version':'1.0','name':name})
        for n in (10,20,30):print(call(0,'tasks',{'api_version':'1.0','algorithm':'numeric','data':n}))
        dest=call(1,'status')
        snapshot=call(0,'snapshot',{'api_version':'1.0','destination_instance':dest['instance']})
        call(1,'restore',snapshot)
        result=call(1,'tasks',{'api_version':'1.0','algorithm':'numeric','data':40})
        assert result['result']['mean']==25
        for target in (1,3):
            quality=call(1,'tasks',{'api_version':'1.0','algorithm':'evaluator','data':{'prediction':2,'target':target}})
        assert quality['result']['mae']==1 and quality['result']['rmse']==1
        print('PASS: mean=25, MAE=1, RMSE=1; source paused, snapshot transferred over HTTP')
    finally:
        for server in servers:server.shutdown();server.server_close()
        for thread in threads:thread.join()
if __name__=='__main__':main()
