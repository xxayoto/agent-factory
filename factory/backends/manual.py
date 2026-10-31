"""Судья-человек для live-режима до M2: показывает кадр и спрашивает вердикт в терминале."""
import subprocess
import sys


class ManualInspector:
    name = 'manual'

    def inspect(self, frame_path, product, scene=None):
        if sys.platform == 'darwin':
            subprocess.run(['open', frame_path], check=False)
        print(f'\nКадр: {frame_path}\nОжидается: {product.attributes}')
        ans = input('Enter — всё верно, или впишите что не так (attr=значение, через пробел): ').strip()
        observed = dict(product.attributes)
        for kv in ans.split():
            if '=' in kv:
                k, v = kv.split('=', 1)
                observed[k] = v
        return observed
