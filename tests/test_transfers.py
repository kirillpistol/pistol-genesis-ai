import base64
import unittest
from level1_core.runtime import Core
from level1_core.transfers import Transfers

class TransferTests(unittest.TestCase):
    def setUp(self):
        self.source,self.dest=Core(),Core()
        self.source.attach('mock');self.source.process('mock',123)
        self.sender,self.receiver=Transfers(self.source),Transfers(self.dest)
        self.meta=self.sender.prepare(self.dest.instance)
        self.identifier=self.meta['transfer_id']
        self.receiver.begin(self.meta)
    def stage(self):
        chunk=self.sender.download(self.identifier,0)
        self.receiver.chunk(self.identifier,0,chunk['chunk_base64'])
        self.receiver.finish(self.identifier)
    def test_resume_and_duplicate_chunk(self):
        raw=self.sender.outgoing['raw']
        chunk=base64.b64encode(raw[:50]).decode()
        self.receiver.chunk(self.identifier,0,chunk)
        self.assertEqual(self.receiver.progress(self.identifier)['next_offset'],50)
        self.receiver.chunk(self.identifier,0,chunk)
        self.assertEqual(self.receiver.progress(self.identifier)['next_offset'],50)
        remaining=self.sender.download(self.identifier,50)
        self.receiver.chunk(self.identifier,50,remaining['chunk_base64'])
        self.receiver.finish(self.identifier)
        self.assertEqual(self.dest.algorithms,{})
        token=self.sender.release(self.identifier)['activation_token']
        self.receiver.activate(self.identifier,token)
        self.receiver.activate(self.identifier,token)
        self.assertTrue(self.dest.process('mock',1)['result']['accepted'])
        with self.assertRaises(ValueError):self.sender.abort(self.identifier)
    def test_checksum_failure_atomic(self):
        raw=bytearray(self.sender.outgoing['raw']);raw[-1]^=1
        self.receiver.chunk(self.identifier,0,base64.b64encode(raw).decode())
        with self.assertRaises(ValueError):self.receiver.finish(self.identifier)
        self.assertEqual(self.dest.algorithms,{})
        self.assertEqual(self.dest.mode,'paused')
    def test_incomplete_atomic_and_rollback(self):
        chunk=base64.b64encode(self.sender.outgoing['raw'][:20]).decode()
        self.receiver.chunk(self.identifier,0,chunk)
        with self.assertRaises(ValueError):self.receiver.finish(self.identifier)
        self.receiver.cancel(self.identifier);self.sender.abort(self.identifier)
        self.assertEqual(self.source.mode,'running')
        self.assertEqual(self.dest.mode,'running')
        with self.assertRaises(ValueError):self.receiver.begin(self.meta)
    def test_staged_rollback(self):
        self.stage()
        self.receiver.cancel(self.identifier);self.sender.abort(self.identifier)
        self.assertTrue(self.source.process('mock',1)['result']['accepted'])
        self.assertEqual(self.dest.algorithms,{})
    def test_bad_token_and_conflicting_duplicates(self):
        chunk=self.sender.download(self.identifier,0)
        self.receiver.chunk(self.identifier,0,chunk['chunk_base64'])
        with self.assertRaises(ValueError):self.receiver.chunk(self.identifier,0,base64.b64encode(b'wrong').decode())
        self.receiver.finish(self.identifier)
        with self.assertRaises(ValueError):self.receiver.activate(self.identifier,'0'*64)
        self.assertEqual(self.dest.mode,'paused')
    def test_no_task_while_receiving(self):
        with self.assertRaises(ValueError):self.dest.attach('mock')
        with self.assertRaises(ValueError):self.dest.restore(self.source.snapshot)
