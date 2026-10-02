import unittest
from level1_core.contracts import check,loads
from level1_core.runtime import Core
class ContractTests(unittest.TestCase):
    def test_v1_accept(self):
        check('task',dict(api_version='1.0',algorithm='mock',data={'name':'Pistol'}))
    def test_reject_incompatible(self):
        for version in ('2.0','1.1',1,None):
            with self.assertRaises(ValueError):check('task',dict(api_version=version,algorithm='mock',data=1))
    def test_reject_extra_and_missing(self):
        for body in ({'algorithm':'mock','data':1},{'api_version':'1.0','algorithm':'mock','data':1,'code':'run'}):
            with self.assertRaises(ValueError):check('task',body)
    def test_depth_nan_duplicate(self):
        for raw in ('{"x":1,"x":2}','{"x":NaN}'):
            with self.assertRaises(ValueError):loads(raw)
        deep=1
        for _ in range(20):deep=[deep]
        with self.assertRaises(ValueError):check('task',dict(api_version='1.0',algorithm='mock',data=deep))
    def test_transfer_and_destination_binding(self):
        source,dest=Core(),Core()
        source.attach('mock');source.process('mock','secret')
        snapshot=source.export(dest.instance)
        self.assertNotIn('secret',str(snapshot))
        self.assertEqual(source.export(dest.instance),snapshot)
        with self.assertRaises(ValueError):source.process('mock',1)
        other=Core()
        with self.assertRaises(ValueError):other.restore(snapshot)
        dest.restore(snapshot);dest.restore(snapshot)
        self.assertEqual(dest.process('mock',1)['revision'],3)
    def test_bad_version_is_atomic(self):
        source,dest=Core(),Core();source.attach('mock')
        snapshot=source.export(dest.instance)
        snapshot['algorithms']['mock']['version']='mock/2'
        with self.assertRaises(ValueError):dest.restore(snapshot)
        self.assertEqual(dest.algorithms,{})
    def test_learning_exchange_absent(self):
        self.assertFalse(hasattr(Core(),'exchange'))
