import unittest
from level1_core.runtime import Core
try:
    from level2_algorithms.numeric import REGISTRY
except ImportError:
    REGISTRY=None
@unittest.skipIf(REGISTRY is None,'Set PYTHONPATH to the separate genesis-level-2 checkout for integration tests')
class NumericIntegrationTests(unittest.TestCase):
    def test_core_transfer_quality_and_atomic_failure(self):
        source,dest=Core(REGISTRY),Core(REGISTRY)
        source.attach('numeric');source.attach('evaluator')
        for x in (10,20,30):source.process('numeric',x)
        with self.assertRaises(ValueError):source.process('numeric','bad')
        self.assertEqual(source.algorithms['numeric'].state()['n'],3)
        dest.restore(source.export(dest.instance))
        self.assertEqual(dest.process('numeric',40)['result']['mean'],25)
        dest.process('evaluator',dict(prediction=2,target=1))
        result=dest.process('evaluator',dict(prediction=2,target=3))['result']
        self.assertEqual(result['mae'],1)
        self.assertEqual(result['rmse'],1)

    def test_algorithm_schemas_match_implementation(self):
        import json
        from pathlib import Path
        from level1_core.contracts import validate
        import level2_algorithms.numeric as numeric
        root=Path(numeric.__file__).resolve().parents[1]/'contracts'/'v1'
        for name,algorithm,record in [('numeric',numeric.Numeric(),10),('evaluator',numeric.Evaluator(),dict(prediction=2,target=1))]:
            schema=json.loads((root/(name+'.input.json')).read_text())
            validate(record,schema)
            algorithm.process(record)
            for invalid in (True,'bad',1e41):
                with self.assertRaises(ValueError):validate(invalid,schema)
                with self.assertRaises(ValueError):algorithm.process(invalid)
