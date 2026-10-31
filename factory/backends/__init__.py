"""Бэкенды исполняют роли агентов: mock — бесплатно и детерминированно, live — платные API.

Интерфейс бэкенда кадров:  generate(req: dict, out_path) -> dict  (обязательно ключ 'raw_size')
Интерфейс инспектора судьи: inspect(frame_path, product, scene=None) -> dict  (наблюдаемые атрибуты;
                            проверка сцены и рамка камня — в inspector.last)
Интерфейс видео:            animate(frame_path, out_path, motion, seconds, width, height) -> None
"""


def frame_backend(name, **kw):
    if name == 'mock':
        from .mock import MockFrames
        return MockFrames(**kw)
    if name == 'runninghub_seedream':
        from .runninghub import SeedreamFrames
        return SeedreamFrames()
    if name == 'wavespeed':
        from .wavespeed import WaveSpeedFrames
        return WaveSpeedFrames(kw['model'], kw.get('resolution', '1k'))
    raise ValueError(f'неизвестный бэкенд кадров: {name}')


def judge_inspector(name, **kw):
    if name == 'claude':
        from .claude_judge import ClaudeInspector
        return ClaudeInspector(**kw)
    if name == 'mock':
        from .mock import MockInspector
        return MockInspector()
    if name == 'manual':
        from .manual import ManualInspector
        return ManualInspector()
    raise ValueError(f'неизвестный инспектор судьи: {name}')


def video_backend(name, **kw):
    if name in ('mock', 'local_kenburns'):
        from .local import KenBurns
        return KenBurns()
    if name == 'wavespeed':
        from .wavespeed import WaveSpeedVideo
        return WaveSpeedVideo(kw['model'], kw.get('durations') or (6, 10), kw.get('resolution'))
    if name == 'runninghub_h3':
        from .runninghub_h3 import H3Video
        return H3Video(kw.get('api_json'))
    raise ValueError(f'неизвестный видеобэкенд: {name}')
