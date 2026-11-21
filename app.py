import asyncio
import ipaddress
import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('franek')

app = FastAPI(title='Franek Downloader API')
MAX_DURATION = 3600
MAX_FILE_BYTES = 250 * 1024 * 1024
MAX_JOBS = 1
semaphore = asyncio.Semaphore(MAX_JOBS)
recent = {}

ALLOWED = ('youtube.com', 'youtu.be', 'youtube-nocookie.com', 'tiktok.com',
           'instagram.com', 'facebook.com', 'fb.watch', 'vimeo.com',
           'dailymotion.com', 'soundcloud.com', 'reddit.com', 'twitter.com',
           'x.com', 'twitch.tv', 'bandcamp.com', 'mixcloud.com',
           'bilibili.com', 'archive.org')


def allowed_host(host):
    return any(host == domain or host.endswith('.' + domain) for domain in ALLOWED)


def validate_url(value):
    try:
        u = urlsplit(value)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.port:
            raise ValueError()
        host = u.hostname.lower().rstrip('.')
        if not allowed_host(host):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise HTTPException(400, 'Obsługiwany jest wyłącznie publiczny link HTTPS do wspieranego serwisu.')


class Job(BaseModel):
    url: str = Field(min_length=10, max_length=2048)
    format: str = Field(default='mp4', pattern='^(mp3|mp4|original)$')
    quality: int = Field(default=192)


@app.get('/health')
def health():
    return {'ok': True, 'service': 'Franek Downloader', 'version': 2}


def cleanup(directory):
    shutil.rmtree(directory, ignore_errors=True)
    semaphore.release()


@app.post('/download')
async def download(job: Job, request: Request):
    validate_url(job.url)
    if job.quality not in (128, 192, 256, 320):
        raise HTTPException(400, 'Nieobsługiwana jakość dźwięku.')

    client = request.client.host if request.client else 'unknown'
    now = time.monotonic()
    for key, val in list(recent.items()):
        if now - val > 120:
            recent.pop(key, None)
    if now - recent.get(client, -1000) < 30:
        raise HTTPException(429, 'Odczekaj 30 sekund przed kolejnym pobieraniem.')
    if semaphore.locked():
        raise HTTPException(503, 'Serwer jest zajęty. Spróbuj za chwilę.')

    recent[client] = now
    await semaphore.acquire()
    directory = tempfile.mkdtemp(prefix='franek-')
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(run_download, job, directory), timeout=240
        )
        path = Path(result)
        if not path.is_file():
            raise HTTPException(422, 'Nie znaleziono pliku wynikowego.')
        if path.stat().st_size > MAX_FILE_BYTES:
            raise HTTPException(413, 'Plik przekracza limit 250 MB.')
        name = re.sub(r'[^\w.() -]+', '_', path.name, flags=re.UNICODE)[:140]
        logger.info('Pobieranie zakonczone: format=%s, rozmiar=%d bajtow', job.format, path.stat().st_size)
        return FileResponse(
            str(path), filename=name, media_type='application/octet-stream',
            background=BackgroundTask(cleanup, directory)
        )
    except HTTPException:
        cleanup(directory)
        raise
    except asyncio.TimeoutError as ex:
        logger.warning('Pobieranie przekroczylo limit czasu (240 s)')
        cleanup(directory)
        raise HTTPException(504, 'Pobieranie trwało ponad 4 minuty. Spróbuj innego materiału.') from ex
    except Exception as ex:
        # Pelny traceback w logach Render; API nie ujawnia adresow i szczegolow serwera.
        logger.exception('Blad pobierania (%s)', type(ex).__name__)
        cleanup(directory)
        raise HTTPException(
            422,
            f'Nie udało się pobrać materiału ({type(ex).__name__}). '
            'Szczegóły błędu znajdują się w logach Render.'
        ) from ex


def run_download(job, directory):
    opts = {
        'outtmpl': os.path.join(directory, '%(title).110s-%(id)s.%(ext)s'),
        'noplaylist': True,
        'max_downloads': 1,
        'restrictfilenames': True,
        'quiet': False,
        'no_warnings': False,
        'socket_timeout': 15,
        'retries': 1,
        'fragment_retries': 1,
        'match_filter': yt_dlp.utils.match_filter_func(f'duration <= {MAX_DURATION}'),
        'max_filesize': MAX_FILE_BYTES,
        'ignoreerrors': False,
    }
    if job.format == 'mp3':
        opts.update(
            format='bestaudio/best',
            postprocessors=[{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': str(job.quality),
            }]
        )
    elif job.format == 'mp4':
        opts.update(
            format='bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/best[height<=720]',
            merge_output_format='mp4'
        )
    else:
        opts.update(format='best[height<=720]/best')

    logger.info('Rozpoczynam pobieranie: serwis=%s, format=%s',
                urlsplit(job.url).hostname, job.format)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([job.url])

    files = [p for p in Path(directory).iterdir()
             if p.is_file() and not p.name.endswith(('.part', '.ytdl'))]
    if not files:
        raise RuntimeError('Brak pliku wynikowego')
    return str(max(files, key=lambda p: p.stat().st_size))
