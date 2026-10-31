"""Бесплатные локальные операции: видео из кадра наездом камеры (ffmpeg zoompan)."""
import shutil
import subprocess


def have_ffmpeg():
    return shutil.which('ffmpeg') is not None


class KenBurns:
    name = 'local_kenburns'

    def animate(self, frame_path, out_path, motion, seconds, width, height, fps=25):
        n = int(seconds * fps)
        vf = (f'scale={width * 2}:{height * 2},zoompan=z=\'min(zoom+{0.12 / n:.5f},1.12)\':d={n}:'
              f'x=\'iw/2-(iw/zoom/2)\':y=\'ih/2-(ih/zoom/2)\':s={width}x{height}:fps={fps},format=yuv420p')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', frame_path, '-vf', vf, '-frames:v', str(n),
                        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', out_path], check=True)
