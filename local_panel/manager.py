import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from level1_core.bindings import digest
from level1_core.contracts import dumps,loads
from level1_core.discovery import client_from
from level1_core.handoff import transfer
from level2_algorithms.numeric import number,Evaluator
from level3_data.bound import BoundAdapter,PartClient

ROOT=Path(__file__).resolve().parents[1]
class Manager:
    def __init__(self):
        self.processes=[];self.temp=None;self.active=None;self.results=[];self.events=[]
        self.values=[];self.quality=None;self.control=None;self.data=None
        self.run_id=None;self.started=None;self.passport=None;self.manifest=None
    def event(self,message):
        self.events.append(dict(time=time.strftime('%H:%M:%S'),message=message,correlation_id=self.run_id))
        self.events=self.events[-80:]
    def command(self,*args):
        return [sys.executable,'-m',*args]
    def stop(self):
        for process in self.processes:
            if process.poll() is None:process.terminate()
        for process in self.processes:
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        self.processes=[]
        if self.temp:self.temp.cleanup();self.temp=None
        self.active=None;self.control=None;self.data=None
    def start(self,values):
        if type(values) is not list or not 4<=len(values)<=64:raise ValueError('Введите от 4 до 64 чисел')
        for value in values:
            number(value)
            if abs(value)>1e40:raise ValueError('Число за пределами диапазона')
        if self.active:raise ValueError('Сначала остановите текущий запуск')
        if not shutil.which('openssl'):raise ValueError('OpenSSL не найден в PATH. Установите OpenSSL и перезапустите терминал.')
        for port in (8443,8444,9443,9444):
            with socket.socket() as sock:
                try:sock.bind(('127.0.0.1',port))
                except OSError:raise ValueError('Порт '+str(port)+' занят. Остановите другой локальный запуск.')
        self.temp=tempfile.TemporaryDirectory(prefix='genesis-');base=Path(self.temp.name)
        self.events=[];self.results=[];self.quality=None;self.values=values
        self.run_id=str(uuid.uuid4());self.started=time.monotonic()
        try:
            from examples.make_test_certs import generate
            from examples.make_binding_demo import create
            cert=generate(base/'certs');data=create(cert,base/'data')
            self.manifest=loads((data/'manifest.json').read_bytes())
            middle=len(values)//2
            for part,records in zip(self.manifest['parts'],(values[:middle],values[middle:])):
                raw=b''.join(dumps(record)+b'\n' for record in records)
                (data/(part['part_id']+'.ndjson')).write_bytes(raw)
                part.update(size=len(raw),checksum=hashlib.sha256(raw).hexdigest(),record_hashes=[digest(v) for v in records])
            self.passport=loads((data/'passport.json').read_bytes())
            self.passport['manifest_sha256']=digest(self.manifest)
            policy=loads((data/'policy.json').read_bytes());policy['bindings']=[self.passport]
            for name,value in [('manifest.json',self.manifest),('passport.json',self.passport),('policy.json',policy)]:
                (data/name).write_bytes(dumps(value))
            for source,port in [('source-0','9443'),('source-1','9444')]:
                args=self.command('level3_data.serve_parts','--manifest',str(data/'manifest.json'),'--source-id',source,'--parts-dir',str(data),'--tls-config',str(data/'source-server.json'),'--port',port)
                self.processes.append(subprocess.Popen(args,cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
            for worker,port in [('worker-a','8443'),('worker-b','8444')]:
                args=self.command('level1_core.server','--port',port,'--tls-config',str(cert/'server.json'),'--algorithm-module','level2_algorithms.bindings_demo','--binding-policy',str(data/'policy.json'),'--worker-id',worker)
                self.processes.append(subprocess.Popen(args,cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
            self.control=client_from(loads((cert/'client.json').read_bytes()))
            self.data=client_from(loads((cert/'data.json').read_bytes()))
            config=loads((data/'parts-client.json').read_bytes());tls=config['tls']
            self.parts=PartClient(tls['ca'],tls['cert'],tls['key'],config['peers'])
            deadline=time.monotonic()+10
            while True:
                try:
                    for url in ('https://localhost:8443','https://localhost:8444'):self.control.request(url,'/v1/health')
                    for part in self.manifest['parts']:self.parts.fetch(part)
                    break
                except (OSError,ValueError):
                    if time.monotonic()>deadline:raise ValueError('Не удалось запустить локальные серверы')
                    time.sleep(.1)
            self.active='https://localhost:8443'
            self.control.request(self.active,'/v1/bindings/select',dict(api_version='1.0',binding_id=self.passport['binding_id']),correlation_id=self.run_id)
            self.event('Паспорт утверждён вручную. mTLS: 2 ядра и 2 источника подключены.')
        except Exception:self.stop();raise
    def consume(self,limit=None):
        if not self.active:raise ValueError('Сначала запустите окружение')
        adapter=BoundAdapter(self.data,self.active,self.passport,self.manifest,self.parts)
        taken=0
        for result in adapter.deliver(correlation_id=self.run_id):
            self.results.append(result);taken+=1
            if limit and taken>=limit:break
        self.event('Обработано записей: '+str(taken)+'. Подтверждённая позиция: '+str(self.cursor()))
        if self.cursor()==len(self.values):
            split=len(self.values)//2;prediction=sum(self.values[:split])/split
            evaluator=Evaluator()
            for target in self.values[split:]:self.quality=evaluator.process(dict(prediction=prediction,target=target))
            self.quality.update(baseline=prediction,train_count=split,holdout_count=len(self.values)-split)
            self.event('Поток завершён. MAE/RMSE: фиксированный baseline по первой половине, оценка на второй.')
    def cursor(self):
        return self.control.request(self.active,'/v1/bindings/status')['binding']['next_record']
    def move(self):
        if self.active!='https://localhost:8443':raise ValueError('Демонстрационный перенос доступен только A → B; для повтора перезапустите окружение')
        transfer(self.control,self.active,'https://localhost:8444',correlation_id=self.run_id)
        self.active='https://localhost:8444'
        self.event('Snapshot проверен и активирован на worker-b. worker-a приостановлен.')
    def test(self):
        if not self.active or self.cursor()!=0:raise ValueError('Сквозной тест требует свежего запуска с позицией 0')
        split=len(self.values)//2
        self.consume(split);self.move();self.consume()
        before=self.cursor();self.consume()
        if self.cursor()!=before or len(self.results)!=len(self.values):raise AssertionError('Duplicate processing')
        self.event('E2E PASS: источник → адаптер → ядро → snapshot → ядро B → numeric → evaluator; повтор без дублей.')
    def snapshot(self):
        nodes=[]
        if self.control:
            for url in ('https://localhost:8443','https://localhost:8444'):
                try:
                    status=self.control.request(url,'/v1/status')
                    binding=self.control.request(url,'/v1/bindings/status')['binding']
                    nodes.append(dict(endpoint=url,status=status,binding=binding))
                except Exception as error:nodes.append(dict(endpoint=url,error=str(error)))
        return dict(active=self.active,nodes=nodes,total=len(self.values),results=self.results[-64:],events=list(self.events),quality=self.quality,passport=self.passport,run_id=self.run_id,elapsed_s=round(time.monotonic()-self.started,2) if self.started else 0)
