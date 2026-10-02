class Mock:
    version = 'mock/1'
    def __init__(self, state=None):
        if state not in (None, {}):
            raise ValueError('Mock state must be empty')
    def process(self, value):
        return {'accepted': True, 'kind': type(value).__name__}
    def state(self):
        return {}

REGISTRY = {'mock': Mock}
