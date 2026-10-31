"""Бюджет прогона. Любая платная операция сначала резервирует деньги; превышение — остановка, а не долг."""
import os
import threading

import yaml

PRICES = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'prices.yaml')


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, limit_usd, prices_path=PRICES):
        self.limit = float(limit_usd)
        self.spent = 0.0
        self.lock = threading.Lock()
        with open(prices_path) as f:
            self.prices = yaml.safe_load(f)

    def price(self, role, backend):
        p = self.prices.get(role)
        return float(p.get(backend, 0.0) if isinstance(p, dict) else p or 0.0)

    def reserve(self, role, backend):
        usd = self.price(role, backend)
        with self.lock:
            if self.spent + usd > self.limit + 1e-9:
                raise BudgetExceeded(f'{role}/{backend}: ${usd:.3f} сверх бюджета (потрачено ${self.spent:.3f} из ${self.limit:.2f})')
            self.spent += usd
        return usd
