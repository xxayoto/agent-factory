"""WaveSpeed (перенос job_search/workflows/ws.py). ПЛАТНО — только --mode live. Ключ: WAVESPEED_API_KEY.
id модели — из config/backends.yaml (сверен с list_models). Реальные траты меряются по балансу до/после прогона."""
import json
import mimetypes
import os
import time
import urllib.request
import uuid

from PIL import Image

API = 'https://api.wavespeed.ai/api/v3'


class WaveSpeedFrames:
    name = 'wavespeed'

    ASPECTS = ['1:1', '2:3', '3:2', '3:4', '4:3', '4:5', '5:4', '9:16', '16:9']

    def __init__(self, model, resolution='1k'):
        if not model:
            raise RuntimeError('не задан wavespeed.frame_model в config/backends.yaml')
        self.model, self.resolution = model, resolution
        self.key = os.environ.get('WAVESPEED_API_KEY') or ''
        if not self.key:
            raise RuntimeError('нет WAVESPEED_API_KEY в окружении')

    def _req(self, method, path, body=None, headers=None, tries=1):
        """tries > 1 — только для идемпотентных запросов (GET статуса, баланс): POST задачи не повторяем, чтобы не платить дважды."""
        h = {'Authorization': f'Bearer {self.key}', **(headers or {})}
        data = body
        if isinstance(body, dict):
            data = json.dumps(body).encode()
            h['Content-Type'] = 'application/json'
        r = urllib.request.Request(API + path, data=data, method=method, headers=h)
        for i in range(tries):
            try:
                with urllib.request.urlopen(r, timeout=120) as resp:
                    return json.load(resp)
            except (OSError, TimeoutError):
                if i == tries - 1:
                    raise
                time.sleep(5 * (i + 1))

    def _upload(self, path):
        b = uuid.uuid4().hex
        ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        body = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{os.path.basename(path)}"\r\n'
                f'Content-Type: {ctype}\r\n\r\n').encode() + open(path, 'rb').read() + f'\r\n--{b}--\r\n'.encode()
        return self._req('POST', '/media/upload/binary', body, {'Content-Type': f'multipart/form-data; boundary={b}'})['data']['download_url']

    def balance(self):
        return float(self._req('GET', '/balance', tries=3)['data']['balance'])

    def aspect(self, w, h):
        return min(self.ASPECTS, key=lambda a: abs(int(a.split(':')[0]) / int(a.split(':')[1]) - w / h))

    def generate(self, req, out_path):
        """id оплаченной задачи сразу пишется в <out>.task: при сбое сети повторный запуск (--resume)
        заберёт готовый результат, а не создаст и не оплатит новую задачу."""
        task_file = out_path + '.task'
        if os.path.exists(task_file):
            task_id, reused = open(task_file).read().strip(), True
        else:
            imgs = list(req['photos']) + ([req['reference']] if req.get('reference') else [])
            body = {'prompt': req['prompt'], 'images': [self._upload(p) for p in imgs],
                    'aspect_ratio': self.aspect(req['width'], req['height']), 'resolution': self.resolution, 'output_format': 'png'}
            task_id, reused = self._req('POST', f'/{self.model}', body)['data']['id'], False
            with open(task_file, 'w') as f:
                f.write(task_id)
        while True:
            res = self._req('GET', f'/predictions/{task_id}/result', tries=5)['data']
            if res['status'] in ('completed', 'failed'):
                break
            time.sleep(3)
        if res['status'] != 'completed':
            raise RuntimeError(f"failed: {res.get('error')}")
        for i in range(4):
            try:
                urllib.request.urlretrieve(res['outputs'][0], out_path)
                break
            except (OSError, TimeoutError):
                if i == 3:
                    raise
                time.sleep(5)
        return {'raw_size': list(Image.open(out_path).size), 'task_id': task_id, 'model': self.model, 'reused_task': reused}
