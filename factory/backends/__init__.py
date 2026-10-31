"""Бэкенды исполняют роли агентов: mock — бесплатно и детерминированно, live — платные API.

Интерфейс бэкенда кадров:  generate(req: dict, out_path) -> dict  (обязательно ключ 'raw_size')
Интерфейс инспектора судьи: inspect(frame_path, product) -> dict  (наблюдаемые атрибуты)
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


def video_backend(name):
    if name in ('mock', 'local_kenburns'):
        from .local import KenBurns
        return KenBurns()
    raise ValueError(f'видеобэкенд {name} появится в M2 (перенос rh_h3.py)')
