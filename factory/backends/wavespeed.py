"""WaveSpeed (перенос job_search/workflows/ws.py). ПЛАТНО — только --mode live. Ключ: WAVESPEED_API_KEY.
Не проверен в M1: id модели берётся из config/backends.yaml и заполняется вручную."""
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

    def __init__(self, model):
        if not model:
            raise RuntimeError('не задан wavespeed.frame_model в config/backends.yaml')
        self.model = model
        self.key = os.environ.get('WAVESPEED_API_KEY') or ''
        if not self.key:
            raise RuntimeError('нет WAVESPEED_API_KEY в окружении')

    def _req(self, method, path, body=None, headers=None):
        h = {'Authorization': f'Bearer {self.key}', **(headers or {})}
        data = body
        if isinstance(body, dict):
            data = json.dumps(body).encode()
            h['Content-Type'] = 'application/json'
        r = urllib.request.Request(API + path, data=data, method=method, headers=h)
        with urllib.request.urlopen(r, timeout=120) as resp:
            return json.load(resp)

    def _upload(self, path):
        b = uuid.uuid4().hex
        ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        body = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{os.path.basename(path)}"\r\n'
                f'Content-Type: {ctype}\r\n\r\n').encode() + open(path, 'rb').read() + f'\r\n--{b}--\r\n'.encode()
        return self._req('POST', '/media/upload/binary', body, {'Content-Type': f'multipart/form-data; boundary={b}'})['data']['download_url']

    def generate(self, req, out_path):
        imgs = list(req['photos']) + ([req['reference']] if req.get('reference') else [])
        job = self._req('POST', f'/{self.model}', {'prompt': req['prompt'], 'images': [self._upload(p) for p in imgs]})['data']
        while True:
            res = self._req('GET', f'/predictions/{job["id"]}/result')['data']
            if res['status'] in ('completed', 'failed'):
                break
            time.sleep(3)
        if res['status'] != 'completed':
            raise RuntimeError(f"failed: {res.get('error')}")
        urllib.request.urlretrieve(res['outputs'][0], out_path)
        return {'raw_size': list(Image.open(out_path).size), 'task_id': job['id']}
