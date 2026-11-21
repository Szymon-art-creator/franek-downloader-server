import asyncio
import ipaddress
import os
import re
import shutil
import socket
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.background import BackgroundTask
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

app = FastAPI(title='Franek Downloader API')
MAX_DURATION = 3600
MAX_FILE_BYTES = 250 * 1024 * 1024
MAX_JOBS = 1
semaphore = asyncio.Semaphore(MAX_JOBS)
recent = {}

# Public supported services only; no arbitrary URLs to prevent SSRF.
ALLOWED = ('youtube.com','youtu.be','youtube-nocookie.com','tiktok.com','instagram.com','facebook.com','fb.watch','vimeo.com','dailymotion.com','soundcloud.com','reddit.com','twitter.com','x.com','twitch.tv','bandcamp.com','mixcloud.com','bilibili.com','archive.org')

def allowed_host(host):
    return any(host == domain or host.endswith('.'+domain) for domain in ALLOWED)

def validate_url(value):
    try:
        u = urlsplit(value)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.port:
            raise ValueError()
        host = u.hostname.lower().rstrip('.')
        if not allowed_host(host):
            raise ValueError()
        # Avoid DNS resolution and private-network URL targets; yt-dlp may still
        # follow provider redirects, so deploy only in a restricted environment.
        try:
            ipaddress.ip_address(host)
            raise ValueError()
        except ValueError:
            pass
        return value
    except Exception:
        raise HTTPException(400, 'Obsługiwany jest wyłącznie publiczny link HTTPS do wspieranego serwisu.')

class Job(BaseModel):
    url: str = Field(min_length=10, max_length=2048)
    format: str = Field(default='mp4', pattern='^(mp3|mp4|original)$')
    quality: int = Field(default=192)

@app.get('/health')
def health():
    return {'ok': True, 'service': 'Franek Downloader', 'version': 1}

@app.post('/download')
async def download(job: Job, request: Request):
    validate_url(job.url)
    if job.quality not in (128,192,256,320):
        raise HTTPException(400, 'Nieobsługiwana jakość dźwięku.')
    client = request.client.host if request.client else 'unknown'
    now = time.monotonic()
    for key, val in list(recent.items()):
        if now - val > 120: recent.pop(key, None)
    if now - recent.get(client, -1000) < 30:
        raise HTTPException(429, 'Odczekaj 30 sekund przed kolejnym pobieraniem.')
    if semaphore.locked():
        raise HTTPException(503, 'Serwer jest zajęty. Spróbuj za chwilę.')
    recent[client] = now
    await semaphore.acquire()
    directory = tempfile.mkdtemp(prefix='franek-')
    try:
        result = await asyncio.wait_for(asyncio.to_thread(run_download, job, directory), timeout=240)
        path = Path(result)
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise HTTPException(413, 'Plik przekracza limit 250 MB.')
        name = re.sub(r'[^\w.() -]+','_',path.name,flags=re.UNICODE)[:140]
        return FileResponse(str(path), filename=name, media_type='application/octet-stream', background=BackgroundTask(lambda: (shutil.rmtree(directory, ignore_errors=True), semaphore.release())))
    except HTTPException:
        shutil.rmtree(directory, ignore_errors=True)
        semaphore.release()
        raise
    except (asyncio.TimeoutError, Exception) as ex:
        shutil.rmtree(directory, ignore_errors=True)
        semaphore.release()
        raise HTTPException(422, 'Nie udało się pobrać materiału. Serwis może blokować serwery hostingowe lub wymagać logowania.') from ex

def run_download(job, directory):
    opts = {
        'outtmpl': os.path.join(directory, '%(title).110s-%(id)s.%(ext)s'),
        'noplaylist': True,
        'max_downloads': 1,
        'restrictfilenames': True,
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 15,
        'retries': 1,
        'fragment_retries': 1,
        'match_filter': yt_dlp.utils.match_filter_func(f'duration <= {MAX_DURATION}'),
        'max_filesize': MAX_FILE_BYTES,
        'ignoreerrors': False,
    }
    if job.format == 'mp3':
        opts.update(format='bestaudio/best', postprocessors=[{'key':'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality':str(job.quality)}])
    elif job.format == 'mp4':
        opts.update(format='bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/best[height<=720]', merge_output_format='mp4')
    else:
        opts.update(format='best[height<=720]/best')
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([job.url])
    files = [p for p in Path(directory).iterdir() if p.is_file() and not p.name.endswith(('.part','.ytdl'))]
    if not files:
        raise RuntimeError('Brak pliku wynikowego')
    return str(max(files,key=lambda p:p.stat().st_size))
