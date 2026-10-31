"""Seedream 5 Pro на RunningHub (перенос job_search/workflows/rh_seedream.py). ПЛАТНО — только --mode live.

Ключ: переменная RUNNINGHUB_API_KEY или файл ~/.config/runninghub/api_key. Ключ не печатается и не пишется в лог.
"""
import base64
import json
import mimetypes
import os
import time
import urllib.request

from PIL import Image

BASE = 'https://www.runninghub.ai/openapi/v2'


def api_key():
    k = os.environ.get('RUNNINGHUB_API_KEY')
    if k:
        return k.strip()
    p = os.path.expanduser('~/.config/runninghub/api_key')
    if not os.path.exists(p):
        raise RuntimeError('нет ключа RunningHub: RUNNINGHUB_API_KEY или ~/.config/runninghub/api_key')
    return open(p).read().strip()


def _b64(path):
    mime = mimetypes.guess_type(path)[0] or 'image/png'
    return f'data:{mime};base64,' + base64.b64encode(open(path, 'rb').read()).decode()


class SeedreamFrames:
    name = 'runninghub_seedream'

    def __init__(self):
        self.key = api_key()

    def _call(self, path, body):
        r = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                   headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {self.key}'})
        for i in range(4):
            try:
                with urllib.request.urlopen(r, timeout=180) as resp:
                    return json.load(resp)
            except OSError:
                if i == 3:
                    raise
                time.sleep(3)

    def generate(self, req, out_path):
        imgs = list(req['photos']) + ([req['reference']] if req.get('reference') else [])
        j = self._call('/seedream-v5-pro/image-to-image', {'prompt': req['prompt'], 'imageUrls': [_b64(p) for p in imgs]})
        task = j.get('taskId')
        if not task:
            raise RuntimeError(f'create failed: {json.dumps(j)[:300]}')
        while True:
            time.sleep(4)
            q = self._call('/query', {'taskId': task})
            if q.get('status') in ('SUCCESS', 'FAILED'):
                break
        if q['status'] != 'SUCCESS':
            raise RuntimeError(f'task failed: {json.dumps(q)[:300]}')
        urllib.request.urlretrieve(q['results'][0]['url'], out_path)
        return {'raw_size': list(Image.open(out_path).size), 'task_id': task, 'usage': q.get('usage')}
