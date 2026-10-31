"""MiniMax H3 (воркфлоу AI Syndicate) на RunningHub — перенос job_search/workflows/rh_h3.py.
ПЛАТНО: через API списывается кошелёк RH (~$0.055 за 5 с, ~5 мин), монеты не тратятся.
Нужен API-JSON воркфлоу (путь — runninghub.h3_api_json в config/backends.yaml)."""
import json
import os
import time
import urllib.request

from .runninghub import api_key

BASE = 'https://www.runninghub.ai'
WF = '2116294161255538689'


class H3Video:
    name = 'runninghub_h3'

    def __init__(self, api_json):
        if not api_json or not os.path.exists(api_json):
            raise RuntimeError('нет API-JSON воркфлоу H3 (runninghub.h3_api_json в config/backends.yaml)')
        self.key, self.api_json = api_key(), api_json

    def _post(self, path, body):
        r = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                   headers={'Content-Type': 'application/json', 'Host': 'www.runninghub.ai'})
        for i in range(4):
            try:
                with urllib.request.urlopen(r, timeout=120) as resp:
                    return json.load(resp)
            except OSError:
                if i == 3:
                    raise
                time.sleep(3)

    def _upload(self, path):
        import mimetypes
        import uuid
        b = uuid.uuid4().hex
        parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                 for k, v in (('apiKey', self.key), ('fileType', 'image'))]
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{os.path.basename(path)}"\r\n'
                     f'Content-Type: {mimetypes.guess_type(path)[0] or "image/png"}\r\n\r\n'.encode() + open(path, 'rb').read() + b'\r\n')
        r = urllib.request.Request(BASE + '/task/openapi/upload', data=b''.join(parts) + f'--{b}--\r\n'.encode(),
                                   headers={'Content-Type': f'multipart/form-data; boundary={b}'})
        with urllib.request.urlopen(r, timeout=120) as resp:
            j = json.load(resp)
        if j.get('code') != 0:
            raise RuntimeError(f'upload failed: {j}')
        return j['data']['fileName']

    def animate(self, frame_path, out_path, motion, seconds, width, height):
        task_file = out_path + '.task'
        if os.path.exists(task_file):
            task = open(task_file).read().strip()
        else:
            p = json.loads(json.load(open(self.api_json))['data']['prompt'])
            # правки из rh_h3.py: ModelSamplingSD3 ломает H3, аудио-реф и zip-нода не нужны
            p['126']['inputs']['model'] = ['154', 0]
            for n in ('155', '143', '147'):
                p.pop(n, None)
            p['136']['inputs'].pop('ref_audios.ref_audio_0', None)
            p['137']['inputs']['image'] = self._upload(frame_path)
            p['138']['inputs']['value'] = motion
            p['132']['inputs']['value'] = seconds
            p['115']['inputs'].update({'aspect_ratio': '9:16 (Portrait Widescreen)', 'megapixels': 0.9})
            p['129']['inputs']['noise_seed'] = int(time.time()) % 10 ** 12
            j = self._post('/task/openapi/create', {'apiKey': self.key, 'workflowId': WF, 'workflow': json.dumps(p)})
            if j.get('code') != 0:
                raise RuntimeError(f'create failed: {j}')
            task = j['data']['taskId']
            with open(task_file, 'w') as f:
                f.write(task)
        while True:
            time.sleep(8)
            st = self._post('/task/openapi/status', {'apiKey': self.key, 'taskId': task}).get('data')
            if st in ('SUCCESS', 'FAILED'):
                break
        o = self._post('/task/openapi/outputs', {'apiKey': self.key, 'taskId': task})
        if st != 'SUCCESS' or o.get('code') != 0:
            raise RuntimeError(f'task {st}: {json.dumps(o)[:300]}')
        vids = [f for f in o['data'] if str(f.get('fileUrl', '')).lower().endswith(('.mp4', '.webm', '.mov'))]
        urllib.request.urlretrieve((vids or o['data'])[0]['fileUrl'], out_path)
        return {'task_id': task, 'model': 'minimax-h3'}
