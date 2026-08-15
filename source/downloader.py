import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests
import yt_dlp

try:
    from yt_dlp.utils import download_range_func
except ImportError:
    download_range_func = None

DOWNLOAD_DIR = Path(os.getenv('V4MD_DOWNLOAD_DIR', '/var/lib/v4-media-downloader/downloads')).expanduser()
WORK_DIR = Path(os.getenv('V4MD_WORK_DIR', '/var/lib/v4-media-downloader/work')).expanduser()
THUMB_DIR = Path(os.getenv('V4MD_THUMB_DIR', '/var/lib/v4-media-downloader/thumbnails')).expanduser()
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
WORK_DIR.mkdir(parents=True, exist_ok=True)
THUMB_DIR.mkdir(parents=True, exist_ok=True)

jobs = []
jobs_lock = threading.Lock()
worker_wakeup = threading.Event()


def detect_source(url: str) -> str:
    value = (url or '').lower()
    if 'twitch.tv' in value:
        return 'Twitch'
    if 'youtube.com' in value or 'youtu.be' in value:
        return 'YouTube'
    return 'Web'


def detect_media_kind(url: str, info=None) -> str:
    source = detect_source(url)
    if source == 'Twitch':
        value = (url or '').lower()
        if 'clips.twitch.tv' in value or '/clip/' in value:
            return 'clip'
        if '/videos/' in value:
            return 'vod'
        if info and info.get('is_live'):
            return 'live'
        return 'twitch'
    if source == 'YouTube':
        return 'live' if info and info.get('is_live') else 'video'
    return 'media'


def common_ydl_options():
    options = {
        'remote_components': {'ejs:github'},
        'quiet': True,
        'no_warnings': True,
    }
    cookie_file = os.getenv('YTDLP_COOKIE_FILE', '').strip()
    if cookie_file and os.path.isfile(cookie_file):
        options['cookiefile'] = cookie_file
    return options


def best_thumbnail(info):
    thumbnail = info.get('thumbnail')
    if thumbnail:
        return thumbnail
    thumbs = info.get('thumbnails') or []
    return thumbs[-1].get('url') if thumbs else None


def available_qualities(info):
    heights = set()
    for fmt in info.get('formats') or []:
        if fmt.get('vcodec') in (None, 'none'):
            continue
        height = fmt.get('height')
        if isinstance(height, (int, float)) and height > 0:
            heights.add(int(height))
    return sorted(heights, reverse=True)


def analyze_url(url: str):
    url = (url or '').strip()
    if not url:
        raise ValueError('No URL provided.')

    options = common_ydl_options()
    options.update({'noplaylist': True, 'skip_download': True})
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise RuntimeError('Media could not be analyzed.')

    # Some extractors may return a single item wrapped as a playlist.
    entries = info.get('entries') if isinstance(info, dict) else None
    if entries:
        info = next((entry for entry in entries if entry), info)

    source = detect_source(url)
    kind = detect_media_kind(url, info)
    duration = info.get('duration')
    qualities = available_qualities(info)

    return {
        'url': info.get('webpage_url') or url,
        'title': info.get('title') or 'Untitled',
        'channel': info.get('channel') or info.get('uploader') or info.get('creator') or '',
        'duration': duration,
        'thumbnail': best_thumbnail(info),
        'source': source,
        'kind': kind,
        'is_live': bool(info.get('is_live')),
        'qualities': qualities,
        'trim_supported': source == 'Twitch' and kind == 'vod' and bool(duration),
        'id': info.get('id'),
    }


def search_youtube(query: str, limit: int = 8):
    limit = max(1, min(int(limit), 20))
    options = common_ydl_options()
    options.update({'extract_flat': True, 'skip_download': True, 'noplaylist': True})
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(f'ytsearch{limit}:{query}', download=False)

    results = []
    for entry in (info or {}).get('entries') or []:
        if not entry:
            continue
        video_id = entry.get('id')
        url = entry.get('webpage_url') or entry.get('url')
        if video_id and (not url or not str(url).startswith('http')):
            url = f'https://www.youtube.com/watch?v={video_id}'
        results.append({
            'id': video_id,
            'title': entry.get('title') or 'Untitled',
            'url': url,
            'channel': entry.get('channel') or entry.get('uploader') or '',
            'duration': entry.get('duration'),
            'thumbnail': best_thumbnail(entry),
        })
    return results


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', '_', name).strip()
    name = re.sub(r'\s+', ' ', name)
    return name[:180] or 'download'


def parse_time_value(value):
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r'\d+(?:\.\d+)?', text):
        return max(0.0, float(text))
    parts = text.split(':')
    if len(parts) not in (2, 3) or any(not re.fullmatch(r'\d+(?:\.\d+)?', p) for p in parts):
        raise ValueError('Time must be specified as HH:MM:SS, MM:SS, or seconds.')
    nums = [float(p) for p in parts]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return nums[0] * 3600 + nums[1] * 60 + nums[2]


def normalize_quality(value):
    value = str(value or 'best').lower().strip()
    if value in {'best', 'auto', ''}:
        return 'best'
    try:
        height = int(value)
        if height < 144 or height > 8640:
            raise ValueError
        return str(height)
    except ValueError:
        return 'best'


def add_urls(items):
    created = []
    with jobs_lock:
        for item in items:
            url = (item.get('url') or '').strip()
            if not url:
                continue
            output_format = (item.get('format') or 'mp3').lower().strip()
            if output_format not in {'mp3', 'mp4'}:
                output_format = 'mp3'
            try:
                trim_start = parse_time_value(item.get('trim_start'))
                trim_end = parse_time_value(item.get('trim_end'))
            except ValueError:
                trim_start = trim_end = None
            if trim_start is not None and trim_end is not None and trim_end <= trim_start:
                trim_end = None
            job = {
                'id': str(uuid.uuid4()),
                'url': url,
                'custom_name': (item.get('custom_name') or '').strip(),
                'format': output_format,
                'quality': normalize_quality(item.get('quality')),
                'trim_start': trim_start,
                'trim_end': trim_end,
                'media_duration': item.get('media_duration'),
                'channel': (item.get('channel') or '').strip(),
                'kind': (item.get('kind') or '').strip(),
                'source': detect_source(url),
                'title': (item.get('title') or 'Waiting for processing').strip(),
                'thumbnail': item.get('thumbnail'),
                'status': 'waiting',
                'stage': 'Queued',
                'progress': 0,
                'download_progress': 0,
                'convert_progress': 0,
                'speed': None,
                'eta': None,
                'filename': None,
                'error': None,
                'created_at': int(time.time()),
            }
            jobs.append(job)
            created.append(dict(job))
    worker_wakeup.set()
    return created


def get_jobs():
    with jobs_lock:
        return [dict(job) for job in jobs]


def update_job(job_id, **values):
    with jobs_lock:
        for job in jobs:
            if job['id'] == job_id:
                job.update(values)
                return True
    return False


def remove_job(job_id, force=False):
    with jobs_lock:
        for idx, job in enumerate(jobs):
            if job['id'] == job_id and (force or job['status'] in {'waiting', 'finished', 'error'}):
                jobs.pop(idx)
                return True
    return False


def retry_job(job_id):
    with jobs_lock:
        for job in jobs:
            if job['id'] == job_id and job['status'] == 'error':
                job.update({
                    'status': 'waiting', 'stage': 'Queued', 'progress': 0,
                    'download_progress': 0, 'convert_progress': 0,
                    'speed': None, 'eta': None, 'error': None,
                })
                worker_wakeup.set()
                return True
    return False


def get_duration(filename):
    result = subprocess.run([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', filename
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError('Duration could not be determined with ffprobe.')
    return float(result.stdout.strip())


def range_options(job):
    start = job.get('trim_start')
    end = job.get('trim_end')
    if start is None and end is None:
        return {}
    if download_range_func is None:
        raise RuntimeError('This yt-dlp version does not support VOD trimming yet. Please update yt-dlp.')
    start = 0.0 if start is None else float(start)
    if end is None:
        end = job.get('media_duration')
        if end is None:
            raise RuntimeError('The media must be analyzed first when using an open-ended VOD trim.')
    end = float(end)
    return {
        'download_ranges': download_range_func(None, [(start, end)]),
        'force_keyframes_at_cuts': True,
    }


def save_thumbnail(job, output_file):
    url = (job.get('thumbnail') or '').strip()
    if not url:
        return None
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        ctype = (response.headers.get('content-type') or '').lower()
        ext = '.png' if 'png' in ctype else '.webp' if 'webp' in ctype else '.jpg'
        media_name = os.path.basename(output_file)
        target = THUMB_DIR / f'{media_name}{ext}'
        for old in THUMB_DIR.glob(f'{media_name}.*'):
            old.unlink(missing_ok=True)
        target.write_bytes(response.content)
        return str(target)
    except Exception:
        return None


def notify_discord(job, output_file):
    webhook = os.getenv('DISCORD_WEBHOOK_URL', '').strip()
    if not webhook:
        return

    def human_duration(value):
        try:
            seconds = max(0, int(float(value)))
        except (TypeError, ValueError):
            return None
        hours, rem = divmod(seconds, 3600)
        minutes, seconds = divmod(rem, 60)
        return f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes}:{seconds:02d}'

    try:
        stat = os.stat(output_file)
        size_mb = stat.st_size / (1024 * 1024)
        fmt = (job.get('format') or 'mp3').upper()
        quality = job.get('quality') or 'best'
        source = job.get('source') or detect_source(job.get('url', ''))
        duration = human_duration(job.get('media_duration'))
        created_at = job.get('created_at')
        elapsed = human_duration(time.time() - created_at) if created_at else None

        fields = [
            {'name': 'Format', 'value': fmt, 'inline': True},
            {'name': 'Size', 'value': f'{size_mb:.1f} MB', 'inline': True},
            {'name': 'Source', 'value': source, 'inline': True},
        ]
        if fmt == 'MP4':
            fields.append({
                'name': 'Quality',
                'value': 'Best available' if quality == 'best' else f'{quality}p',
                'inline': True,
            })
        if duration:
            fields.append({'name': 'Duration', 'value': duration, 'inline': True})
        if elapsed:
            fields.append({'name': 'Processed in', 'value': elapsed, 'inline': True})
        if job.get('channel'):
            fields.append({'name': 'Channel / Creator', 'value': str(job['channel'])[:1024], 'inline': False})
        if job.get('trim_start') is not None or job.get('trim_end') is not None:
            start = human_duration(job.get('trim_start')) or 'Start'
            end = human_duration(job.get('trim_end')) or 'End'
            fields.append({'name': 'Trim', 'value': f'{start} → {end}', 'inline': True})
        fields.append({'name': 'File', 'value': os.path.basename(output_file)[:1024], 'inline': False})

        media_url = (job.get('url') or '').strip()
        embed = {
            'title': f'✓ {fmt} Download completed',
            'description': (job.get('title') or 'Unknown title')[:4096],
            'color': 0x54D598,
            'fields': fields,
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'footer': {'text': 'V4 Media Downloader · Made by V4M0N0S'},
        }
        if media_url.startswith(('http://', 'https://')):
            embed['url'] = media_url
        if job.get('thumbnail'):
            embed['thumbnail'] = {'url': job['thumbnail']}

        response = requests.post(
            webhook,
            json={
                'username': 'V4 Media Downloader',
                'embeds': [embed],
                'allowed_mentions': {'parse': []},
            },
            timeout=10,
        )
        response.raise_for_status()
    except Exception:
        pass


def finish_job(job_id, output_file):
    update_job(job_id, status='finished', stage='Finished', progress=100,
               download_progress=100, convert_progress=100,
               filename=os.path.basename(output_file), speed=None, eta=None)
    finished_job = next((j for j in get_jobs() if j['id'] == job_id), {})
    save_thumbnail(finished_job, output_file)
    notify_discord(finished_job, output_file)
    remove_job(job_id, force=True)


def make_hook(job_id, download_weight=100):
    def hook(data):
        if data.get('status') == 'downloading':
            downloaded = data.get('downloaded_bytes', 0)
            total = data.get('total_bytes') or data.get('total_bytes_estimate')
            percent = (downloaded / total * 100) if total else 0
            update_job(job_id, status='downloading', stage='Download',
                       download_progress=round(percent, 1),
                       progress=round(percent * download_weight / 100, 1),
                       speed=data.get('_speed_str'), eta=data.get('_eta_str'))
        elif data.get('status') == 'finished':
            update_job(job_id, download_progress=100, progress=download_weight,
                       stage='Download completed', speed=None, eta=None)
    return hook


def convert_to_mp3(job_id, input_file, title):
    job = next((j for j in get_jobs() if j['id'] == job_id), {})
    custom_name = safe_filename(job.get('custom_name') or title)
    final_file = DOWNLOAD_DIR / f'{custom_name}.mp3'
    temp_file = WORK_DIR / f'{job_id}.mp3'
    duration = get_duration(input_file)
    update_job(job_id, status='converting', stage='Creating MP3', progress=50, convert_progress=0)

    cmd = ['ffmpeg', '-y', '-i', input_file, '-vn', '-codec:a', 'libmp3lame', '-q:a', '2',
           '-metadata', f'title={title}', '-progress', 'pipe:1', '-nostats', str(temp_file)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    for line in proc.stdout:
        if line.strip().startswith('out_time_ms='):
            try:
                current = int(line.strip().split('=', 1)[1]) / 1_000_000
                percent = max(0, min(100, current / duration * 100))
                update_job(job_id, convert_progress=round(percent, 1), progress=round(50 + percent / 2, 1))
            except Exception:
                pass
    proc.wait()
    if proc.returncode != 0:
        err = proc.stderr.read()[-1500:]
        temp_file.unlink(missing_ok=True)
        raise RuntimeError(f'FFmpeg failed: {err}')
    try:
        os.remove(input_file)
    except OSError:
        pass
    os.replace(temp_file, final_file)
    finish_job(job_id, str(final_file))
    return str(final_file)


def download_audio(job):
    job_id = job['id']
    job_dir = WORK_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    options = common_ydl_options()
    options.update({
        'format': 'bestaudio/best',
        'outtmpl': str(job_dir / '%(id)s.%(ext)s'),
        'noplaylist': True,
        'progress_hooks': [make_hook(job_id, 50)],
        'postprocessors': [],
    })
    options.update(range_options(job))
    update_job(job_id, status='downloading', stage='Analyzing media', progress=0)
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(job['url'], download=True)
        title = info.get('title') or 'Unknown video'
        filename = ydl.prepare_filename(info)
        update_job(job_id, title=title, thumbnail=best_thumbnail(info), download_progress=100, progress=50)
    return filename, title


def mp4_format_selector(quality):
    if quality == 'best':
        return 'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b'
    h = int(quality)
    return (
        f'bv*[ext=mp4][height<={h}]+ba[ext=m4a]/'
        f'b[ext=mp4][height<={h}]/'
        f'bv*[height<={h}]+ba/b[height<={h}]/best'
    )


def download_mp4(job):
    job_id = job['id']
    job_dir = WORK_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    update_job(job_id, status='downloading', stage='Analyzing media', progress=0)

    metadata_options = common_ydl_options()
    metadata_options.update({'noplaylist': True, 'skip_download': True})
    with yt_dlp.YoutubeDL(metadata_options) as ydl:
        info = ydl.extract_info(job['url'], download=False)

    title = info.get('title') or 'Unknown video'
    base_name = safe_filename(job.get('custom_name') or title)
    output_template = str(job_dir / f'{base_name}.%(ext)s')
    update_job(job_id, title=title, thumbnail=best_thumbnail(info))

    def post_hook(data):
        status = data.get('status')
        if status == 'started':
            update_job(job_id, status='converting', stage='Processing MP4', progress=95)
        elif status == 'finished':
            update_job(job_id, progress=99, convert_progress=100)

    options = common_ydl_options()
    options.update({
        'format': mp4_format_selector(job.get('quality', 'best')),
        'outtmpl': output_template,
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'progress_hooks': [make_hook(job_id, 95)],
        'postprocessor_hooks': [post_hook],
    })
    options.update(range_options(job))

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([job['url']])

    temp_output = job_dir / f'{base_name}.mp4'
    if not temp_output.exists():
        candidates = sorted(job_dir.glob(f'{base_name}.*'), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            candidate = candidates[0]
            if candidate.suffix.lower() != '.mp4':
                converted = job_dir / f'{base_name}.mp4'
                subprocess.run(['ffmpeg', '-y', '-i', str(candidate), '-c', 'copy', str(converted)],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                candidate.unlink(missing_ok=True)
                temp_output = converted
            else:
                temp_output = candidate
    if not temp_output.exists():
        raise RuntimeError('MP4 file was not found after download.')

    final_file = DOWNLOAD_DIR / f'{base_name}.mp4'
    os.replace(temp_output, final_file)
    shutil.rmtree(job_dir, ignore_errors=True)
    finish_job(job_id, str(final_file))
    return str(final_file)


def process_job(job):
    try:
        if job.get('format') == 'mp4':
            download_mp4(job)
        else:
            source_file, title = download_audio(job)
            convert_to_mp3(job['id'], source_file, title)
            shutil.rmtree(WORK_DIR / job['id'], ignore_errors=True)
    except Exception as exc:
        update_job(job['id'], status='error', stage='Error', error=str(exc), speed=None, eta=None)


def worker():
    while True:
        current = None
        with jobs_lock:
            for job in jobs:
                if job['status'] == 'waiting':
                    job['status'] = 'starting'
                    job['stage'] = 'Starting'
                    current = dict(job)
                    break
        if current:
            process_job(current)
        else:
            worker_wakeup.wait(1)
            worker_wakeup.clear()


threading.Thread(target=worker, daemon=True).start()
