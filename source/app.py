import os
import tempfile
import zipfile
from pathlib import Path

from flask import (
    Flask, after_this_request, jsonify, render_template, request,
    send_file, send_from_directory
)


def load_env(path='/etc/v4-media-downloader/config.env'):
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()

from downloader import (
    DOWNLOAD_DIR, THUMB_DIR, add_urls, get_jobs, remove_job, retry_job,
    search_youtube, analyze_url
)

app = Flask(__name__)
LOCALE_DIR = Path(__file__).with_name('locales')

COOKIE_DIR = Path(os.getenv('V4MD_CONFIG_DIR', '/etc/v4-media-downloader')).expanduser()
COOKIE_FILE = Path(os.getenv('V4MD_COOKIE_FILE', str(COOKIE_DIR / 'youtube-cookies.txt'))).expanduser()


def cookie_status():
    configured = bool(os.getenv('YTDLP_COOKIE_FILE', '').strip())
    path = Path(os.getenv('YTDLP_COOKIE_FILE', str(COOKIE_FILE))).expanduser()
    return {
        'configured': configured and path.is_file() and path.stat().st_size > 0,
        'managed': path == COOKIE_FILE,
    }


def save_cookie_text(content: str):
    content = (content or '').strip()
    if not content:
        raise ValueError('Cookie content is empty.')
    if len(content.encode('utf-8')) > 1024 * 1024:
        raise ValueError('Cookie file is too large.')
    # yt-dlp expects Netscape/Mozilla cookie-jar format for --cookies.
    if '# Netscape HTTP Cookie File' not in content and '# HTTP Cookie File' not in content:
        raise ValueError('Please paste cookies in Netscape/Mozilla cookies.txt format.')
    cookie_lines = [line for line in content.splitlines() if line and not line.startswith('#')]
    if not any('\t' in line and ('youtube.com' in line.lower() or 'google.com' in line.lower()) for line in cookie_lines):
        raise ValueError('No YouTube/Google cookies were detected in the provided content.')
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = COOKIE_FILE.with_suffix('.tmp')
    tmp.write_text(content + '\n', encoding='utf-8')
    os.chmod(tmp, 0o600)
    tmp.replace(COOKIE_FILE)
    os.chmod(COOKIE_FILE, 0o600)
    os.environ['YTDLP_COOKIE_FILE'] = str(COOKIE_FILE)



def media_paths():
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return [
        p for p in DOWNLOAD_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in {'.mp3', '.mp4'}
    ]


def thumbnail_for(media_name):
    if not THUMB_DIR.exists():
        return None
    matches = sorted(THUMB_DIR.glob(f'{media_name}.*'))
    return matches[0].name if matches else None


def delete_media_and_thumbnail(path: Path):
    media_name = path.name
    path.unlink(missing_ok=True)
    if THUMB_DIR.exists():
        for thumb in THUMB_DIR.glob(f'{media_name}.*'):
            try:
                thumb.unlink()
            except OSError:
                pass


@app.route('/')
def index():
    return render_template('index.html')


def available_locales():
    result = []
    if not LOCALE_DIR.exists():
        return result
    for path in sorted(LOCALE_DIR.glob('*.json')):
        try:
            import json
            data = json.loads(path.read_text(encoding='utf-8'))
            meta = data.get('meta') or {}
            code = str(meta.get('code') or path.stem).strip()
            if not code or code != path.stem:
                continue
            result.append({
                'code': code,
                'name': meta.get('name') or code,
                'native_name': meta.get('native_name') or meta.get('name') or code,
                'locale': meta.get('locale') or code,
            })
        except Exception:
            continue
    return result


@app.route('/api/locales')
def locales_api():
    return jsonify({'locales': available_locales(), 'default': 'de'})


@app.route('/locales/<code>.json')
def locale_file(code):
    safe_code = ''.join(ch for ch in code if ch.isalnum() or ch in {'-', '_'})
    if safe_code != code or not (LOCALE_DIR / f'{safe_code}.json').exists():
        return jsonify({'error': 'Locale not found.'}), 404
    return send_from_directory(str(LOCALE_DIR), f'{safe_code}.json', mimetype='application/json')



@app.route('/api/cookies', methods=['GET'])
def cookies_status_api():
    return jsonify({'success': True, **cookie_status()})


@app.route('/api/cookies', methods=['POST'])
def cookies_import_api():
    data = request.get_json(silent=True) or {}
    try:
        save_cookie_text(data.get('cookies') or '')
        return jsonify({'success': True, **cookie_status()})
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except OSError:
        return jsonify({'success': False, 'error': 'Cookie file could not be saved.'}), 500


@app.route('/api/cookies', methods=['DELETE'])
def cookies_delete_api():
    try:
        configured = Path(os.getenv('YTDLP_COOKIE_FILE', str(COOKIE_FILE))).expanduser()
        if configured == COOKIE_FILE:
            COOKIE_FILE.unlink(missing_ok=True)
            os.environ.pop('YTDLP_COOKIE_FILE', None)
        return jsonify({'success': True, **cookie_status()})
    except OSError:
        return jsonify({'success': False, 'error': 'Cookie file could not be removed.'}), 500


@app.route('/api/add', methods=['POST'])
def add():
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    custom_name = (data.get('custom_name') or '').strip()
    output_format = (data.get('format') or 'mp3').lower().strip()
    quality = (data.get('quality') or 'best').strip()
    trim_start = data.get('trim_start')
    trim_end = data.get('trim_end')
    if output_format not in {'mp3', 'mp4'}:
        return jsonify({'success': False, 'error': 'Invalid format.'}), 400
    if not url:
        return jsonify({'success': False, 'error': 'No URL provided.'}), 400
    created = add_urls([{'url': url, 'custom_name': custom_name, 'format': output_format, 'quality': quality, 'trim_start': trim_start, 'trim_end': trim_end, 'title': data.get('title') or '', 'thumbnail': data.get('thumbnail'), 'media_duration': data.get('media_duration'), 'channel': data.get('channel') or '', 'kind': data.get('kind') or ''}])
    return jsonify({'success': True, 'count': len(created)})


@app.route('/api/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded.'}), 400
    file = request.files['file']
    try:
        content = file.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        return jsonify({'success': False, 'error': 'TXT file must be UTF-8 encoded.'}), 400

    output_format = (request.form.get('format') or 'mp3').lower().strip()
    if output_format not in {'mp3', 'mp4'}:
        return jsonify({'success': False, 'error': 'Invalid format.'}), 400
    quality = (request.form.get('quality') or 'best').strip()

    items = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        items.append({'url': line, 'custom_name': '', 'format': output_format, 'quality': quality})
    if not items:
        return jsonify({'success': False, 'error': 'No URLs found.'}), 400
    created = add_urls(items)
    return jsonify({'success': True, 'count': len(created)})


@app.route('/api/analyze', methods=['POST'])
def analyze_api():
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    if not url:
        return jsonify({'success': False, 'error': 'No URL provided.'}), 400
    try:
        return jsonify({'success': True, 'media': analyze_url(url)})
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/search')
def search_api():
    query = (request.args.get('q') or '').strip()
    if len(query) < 2:
        return jsonify({'success': False, 'error': 'Search query is too short.'}), 400
    try:
        results = search_youtube(query, limit=8)
        return jsonify({'success': True, 'results': results})
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/jobs')
def jobs_api():
    return jsonify({'jobs': get_jobs()})


@app.route('/api/jobs/<job_id>/retry', methods=['POST'])
def retry_api(job_id):
    return jsonify({'success': retry_job(job_id)})


@app.route('/api/jobs/<job_id>', methods=['DELETE'])
def delete_job_api(job_id):
    return jsonify({'success': remove_job(job_id)})


@app.route('/api/files')
def files_api():
    result = []
    for path in media_paths():
        try:
            stat = path.stat()
        except OSError:
            continue
        thumb = thumbnail_for(path.name)
        result.append({
            'name': path.name,
            'format': path.suffix.lower().lstrip('.'),
            'size': stat.st_size,
            'mtime': int(stat.st_mtime),
            'thumbnail': f'/thumbnails/{thumb}' if thumb else None,
            'stream_url': f'/media/{path.name}',
        })
    result.sort(key=lambda x: x['mtime'], reverse=True)
    return jsonify(result)


@app.route('/api/files/<path:filename>', methods=['DELETE'])
def delete_file(filename):
    safe_name = Path(filename).name
    media = DOWNLOAD_DIR / safe_name
    if media.suffix.lower() not in {'.mp3', '.mp4'} or not media.exists():
        return jsonify({'success': False, 'error': 'File not found.'}), 404
    try:
        delete_media_and_thumbnail(media)
        return jsonify({'success': True})
    except OSError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/delete-all', methods=['POST'])
def delete_all():
    files = media_paths()
    deleted = 0
    errors = []
    for path in files:
        try:
            delete_media_and_thumbnail(path)
            deleted += 1
        except OSError as exc:
            errors.append(f'{path.name}: {exc}')
    if THUMB_DIR.exists():
        for thumb in THUMB_DIR.iterdir():
            if thumb.is_file():
                try:
                    thumb.unlink()
                except OSError as exc:
                    errors.append(f'{thumb.name}: {exc}')
    return jsonify({
        'success': not errors,
        'deleted': deleted,
        'errors': errors,
    }), (200 if not errors else 500)


@app.route('/api/download-all')
def download_all():
    files = media_paths()
    if not files:
        return jsonify({'success': False, 'error': 'No completed files are available.'}), 404

    tmp = tempfile.NamedTemporaryFile(prefix='v4-media-library-', suffix='.zip', delete=False)
    tmp_path = tmp.name
    tmp.close()

    with zipfile.ZipFile(tmp_path, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for path in files:
            zf.write(path, arcname=path.name)

    @after_this_request
    def cleanup(response):
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return response

    return send_file(tmp_path, as_attachment=True, download_name='v4-media-library.zip')


@app.route('/downloads/<path:filename>')
def download_file(filename):
    return send_from_directory(str(DOWNLOAD_DIR), filename, as_attachment=True)


@app.route('/media/<path:filename>')
def stream_file(filename):
    return send_from_directory(str(DOWNLOAD_DIR), filename, as_attachment=False, conditional=True)


@app.route('/thumbnails/<path:filename>')
def thumbnail_file(filename):
    return send_from_directory(str(THUMB_DIR), filename, as_attachment=False, conditional=True)


if __name__ == '__main__':
    host = os.getenv('V4MD_HOST', '0.0.0.0')
    port = int(os.getenv('V4MD_PORT', '5000'))
    app.run(host=host, port=port, debug=False, threaded=True)
