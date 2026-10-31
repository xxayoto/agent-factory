import time


class Agent:
    """Агент = роль + свой инструмент + контракт вход/выход. Время и события пишутся в журнал прогона."""
    name = 'agent'

    def __init__(self, log):
        self.log = log

    def timed(self, task, fn, *a, **kw):
        t = time.time()
        try:
            return fn(*a, **kw)
        finally:
            self.log.add_time(self.name, time.time() - t)
