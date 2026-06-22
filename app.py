import os
import uuid
import subprocess
import time
import threading
import PIL.Image

# Monkeypatch PIL.Image.ANTIALIAS for compatibility with moviepy and newer Pillow versions
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS

from flask import Flask, request, jsonify, send_from_directory, render_template

from utils.audio_engine import (
    generate_speech, create_mixed_audio, generate_preview,
    generate_ai_video,
    VOICES, VOICE_STYLES, MOOD_LABELS, AGE_PRESETS
)
from utils.video_effects import concatenate_clips, apply_copyright_filters, slice_video

app = Flask(__name__, static_folder='static', static_url_path='')

# Configuration
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
OUTPUT_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# ── Write Cookies File from Environment Variable if present ──
# IMPORTANT: Store in app root, NOT uploads/, so the cleanup thread never deletes it
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
COOKIES_FILE_PATH = os.path.join(APP_ROOT, 'yt_cookies.txt')
yt_cookies_content = os.environ.get('YT_COOKIES')
if yt_cookies_content:
    try:
        import json as _json
        content_to_write = yt_cookies_content.strip()
        # Auto-convert JSON array format to Netscape format that yt-dlp reads
        if content_to_write.startswith('[') or content_to_write.startswith('{'):
            try:
                cookies_list = _json.loads(content_to_write)
                if isinstance(cookies_list, list):
                    netscape_lines = [
                        '# Netscape HTTP Cookie File',
                        '# http://curl.haxx.se/rfc/cookie_spec.html',
                        '# This is a generated file!  Do not edit.\n'
                    ]
                    for c in cookies_list:
                        if isinstance(c, dict):
                            domain  = c.get('domain', '')
                            flag    = 'TRUE' if domain.startswith('.') or not c.get('hostOnly', True) else 'FALSE'
                            path    = c.get('path', '/')
                            secure  = 'TRUE' if c.get('secure', False) else 'FALSE'
                            try:
                                expiry = str(int(float(c.get('expirationDate', 0))))
                            except (ValueError, TypeError):
                                expiry = '0'
                            name  = c.get('name', '')
                            value = c.get('value', '')
                            netscape_lines.append(f'{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}')
                    content_to_write = '\n'.join(netscape_lines)
                    print(f'[startup] Converted {len(cookies_list)} JSON cookies → Netscape format.')
            except Exception as _je:
                print(f'[startup] JSON parse failed, treating as raw text: {_je}')
        with open(COOKIES_FILE_PATH, 'w', encoding='utf-8') as _f:
            _f.write(content_to_write)
        print(f'[startup] cookies written → {COOKIES_FILE_PATH} ({os.path.getsize(COOKIES_FILE_PATH)} bytes)')
    except Exception as _e:
        print(f'[startup] Failed to write cookies: {_e}')
else:
    print('[startup] No YT_COOKIES env var found — downloads will run without cookies.')

# ── Background Cleanup Thread (30-minute expiration) ──
def start_cleanup_thread():
    def cleanup_loop():
        # wait a bit for startup to complete before the first sweep
        time.sleep(10)
        while True:
            try:
                now = time.time()
                for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER]:
                    if not os.path.exists(folder):
                        continue
                    for f in os.listdir(folder):
                        # Never delete the cookies file
                        if f.startswith('.') or f == '.gitkeep' or f == 'yt_cookies.txt' or f == 'cookies.txt':
                            continue
                        fp = os.path.join(folder, f)
                        if os.path.isfile(fp):
                            mtime = os.path.getmtime(fp)
                            # 1800 seconds = 30 minutes
                            if now - mtime > 1800:
                                try:
                                    os.remove(fp)
                                    print(f"[background-cleanup] Removed expired file: {f}")
                                except Exception as err:
                                    print(f"[background-cleanup] Failed to delete {f}: {err}")
            except Exception as e:
                print(f"[background-cleanup] Error: {e}")
            time.sleep(300) # Run every 5 minutes

    t = threading.Thread(target=cleanup_loop, daemon=True)
    t.start()

start_cleanup_thread()


# ── Clean up any leftover temp/partial files on startup ──
for _f in os.listdir(OUTPUT_FOLDER):
    if '.temp' in _f or 'TEMP_MPY' in _f or _f.endswith('.part'):
        try:
            os.remove(os.path.join(OUTPUT_FOLDER, _f))
            print(f'[cleanup] Removed temp file: {_f}')
        except Exception:
            pass

@app.route('/api/debug')
def debug_info():
    """Debug endpoint — check cookies, yt-dlp version, env."""
    has_cookies = os.path.exists(COOKIES_FILE_PATH) and os.path.getsize(COOKIES_FILE_PATH) > 0
    cookie_lines = 0
    cookie_preview = ''
    if has_cookies:
        try:
            with open(COOKIES_FILE_PATH, 'r') as _f:
                lines = _f.readlines()
                cookie_lines = len([l for l in lines if l.strip() and not l.startswith('#')])
                cookie_preview = lines[3][:80] if len(lines) > 3 else '(empty)'
        except Exception as e:
            cookie_preview = str(e)

    import subprocess as _sp
    ytdlp_version = 'unknown'
    try:
        r = _sp.run(['yt-dlp', '--version'], capture_output=True, text=True, timeout=5)
        ytdlp_version = r.stdout.strip()
    except Exception:
        pass

    return jsonify({
        'cookies_file_path': COOKIES_FILE_PATH,
        'cookies_loaded': has_cookies,
        'cookie_entries': cookie_lines,
        'cookie_preview': cookie_preview,
        'yt_cookies_env_set': bool(os.environ.get('YT_COOKIES')),
        'yt_proxy_set': bool(os.environ.get('YT_PROXY')),
        'ytdlp_version': ytdlp_version,
    })


# Helper to execute shell commands (e.g. yt-dlp using local venv)
def get_pip_binary(binary_name):
    venv_bin = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'venv', 'bin', binary_name)
    if os.path.exists(venv_bin):
        return venv_bin
    return binary_name


def _extract_video_id(url):
    """Extract YouTube video ID from any YouTube URL format."""
    import re
    patterns = [
        r'(?:v=|youtu\.be/|/embed/|/v/|/shorts/)([A-Za-z0-9_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def download_via_cobalt(url, output_path):
    """
    Download via cobalt.tools API — works from datacenter IPs.
    Tries multiple public cobalt instances with both old and new API formats.
    """
    import requests as _req
    instances = [
        'https://api.cobalt.tools',
        'https://cobalt.api.timelessnesses.me',
        'https://cobalt.tools.nadeko.net',
    ]
    # Try both v7 (new) and v6 (old) payload formats
    payloads = [
        # cobalt v7+ format
        {
            'url': url,
            'videoQuality': '720',
            'filenameStyle': 'basic',
            'downloadMode': 'auto',
        },
        # cobalt v6 format (older instances)
        {
            'url': url,
            'vQuality': '720',
            'filenamePattern': 'basic',
            'isAudioOnly': False,
            'disableMetadata': True,
        },
    ]
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
    for instance in instances:
        for payload in payloads:
            try:
                print(f'[cobalt] Trying {instance} ...')
                resp = _req.post(f'{instance}/', json=payload, headers=headers, timeout=20)
                if resp.status_code not in (200, 201):
                    print(f'[cobalt] {instance} returned HTTP {resp.status_code}')
                    break  # try next instance, not next payload
                data = resp.json()
                status = data.get('status', '')
                dl_url = data.get('url', '')
                if status in ('stream', 'tunnel', 'redirect', 'local', 'picker') and dl_url:
                    print(f'[cobalt] Got URL ({status}), downloading ...')
                    with _req.get(dl_url, stream=True, timeout=300,
                                  headers={'User-Agent': 'Mozilla/5.0'}) as r:
                        r.raise_for_status()
                        with open(output_path, 'wb') as f:
                            for chunk in r.iter_content(65536):
                                f.write(chunk)
                    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                        print(f'[cobalt] Success! {os.path.getsize(output_path)//1024} KB')
                        return True
                else:
                    print(f'[cobalt] Unexpected status from {instance}: {status} | {data}')
                    break
            except Exception as e:
                print(f'[cobalt] Error with {instance}: {e}')
                break
    return False


def download_via_pytubefix(url, output_path):
    """
    Download via pytubefix — uses different request patterns than yt-dlp
    and often works from datacenter IPs where yt-dlp SSL-fails.
    """
    try:
        from pytubefix import YouTube
        from pytubefix.cli import on_progress
        
        # Try multiple clients to find one that YouTube doesn't block or prompt on.
        # 'WEB' triggers node-based signature generation if node is available.
        # 'ANDROID' is the mobile client.
        clients = ['WEB', 'ANDROID', 'ANDROID_VR']
        
        for client in clients:
            try:
                print(f'[pytubefix] Trying client={client} for {url} ...')
                yt = YouTube(
                    url,
                    client=client,
                    on_progress_callback=on_progress,
                    use_oauth=False,
                    allow_oauth_cache=False,
                    use_po_token=False  # Do NOT use True, it prompts interactively in terminal
                )
                # Try progressive (combined audio+video) MP4 first
                stream = (
                    yt.streams
                      .filter(progressive=True, file_extension='mp4')
                      .order_by('resolution')
                      .last()  # highest resolution
                )
                if not stream:
                    # Fall back to any MP4
                    stream = yt.streams.filter(file_extension='mp4').first()
                if not stream:
                    print(f'[pytubefix] No suitable stream found with client={client}')
                    continue
                print(f'[pytubefix] Downloading {stream.resolution} stream with client={client} ...')
                import tempfile, shutil
                # pytubefix downloads to a directory, so use a temp dir
                tmp_dir = tempfile.mkdtemp()
                try:
                    dl_path = stream.download(output_path=tmp_dir, filename='video.mp4')
                    if dl_path and os.path.exists(dl_path) and os.path.getsize(dl_path) > 0:
                        shutil.move(dl_path, output_path)
                        print(f'[pytubefix] Success with client={client}! {os.path.getsize(output_path)//1024} KB')
                        return True
                finally:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception as client_err:
                print(f'[pytubefix] Client {client} failed: {client_err}')
                
    except Exception as e:
        print(f'[pytubefix] General Error: {e}')
    return False


def download_via_invidious(url, output_path):
    """
    Download via public Invidious instances — these proxy YouTube traffic
    and are not datacenter-blocked.
    """
    import requests as _req
    vid_id = _extract_video_id(url)
    if not vid_id:
        print('[invidious] Could not extract video ID')
        return False

    # Default fallback list
    instances = [
        'https://inv.nadeko.net',
        'https://invidious.nerdvpn.de',
        'https://invidious.f5.si',
        'https://yt.chocolatemoo53.com',
        'https://inv.thepixora.com',
    ]

    try:
        print('[invidious] Querying public instances API for live hosts ...')
        api_resp = _req.get('https://api.invidious.io/instances.json', timeout=10)
        if api_resp.status_code == 200:
            live_instances = []
            for item in api_resp.json():
                try:
                    info = item[1]
                    monitor = info.get('monitor')
                    if monitor and not monitor.get('down', True) and monitor.get('last_status') == 200:
                        uri = info.get('uri')
                        if uri:
                            live_instances.append(uri.rstrip('/'))
                except Exception:
                    pass
            if live_instances:
                print(f'[invidious] Found {len(live_instances)} live healthy instances.')
                # Prioritize live instances, appending defaults just in case
                instances = live_instances + [inst for inst in instances if inst not in live_instances]
    except Exception as api_err:
        print(f'[invidious] Failed to fetch live instances: {api_err}. Using defaults.')

    for instance in instances:
        instance = instance.rstrip('/')
        try:
            print(f'[invidious] Querying {instance} for {vid_id} ...')
            api = f'{instance}/api/v1/videos/{vid_id}?fields=formatStreams'
            resp = _req.get(api, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
            if resp.status_code != 200:
                print(f'[invidious] {instance} returned HTTP {resp.status_code}')
                continue
            streams = resp.json().get('formatStreams', [])
            # Pick best MP4 at or below 720p
            best = None
            for s in streams:
                if s.get('container') == 'mp4':
                    try:
                        q = int(s.get('qualityLabel', '0p').replace('p', '').split()[0])
                    except Exception:
                        q = 0
                    if q <= 720:
                        if best is None or q > int(best.get('qualityLabel', '0p').replace('p', '').split()[0]):
                            best = s
            if best and best.get('url'):
                stream_url = best['url']
                print(f'[invidious] Downloading {best.get("qualityLabel")} from {instance} ...')
                with _req.get(stream_url, stream=True, timeout=300,
                              headers={'User-Agent': 'Mozilla/5.0'}) as r:
                    r.raise_for_status()
                    with open(output_path, 'wb') as f:
                        for chunk in r.iter_content(65536):
                            f.write(chunk)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    print(f'[invidious] Success! {os.path.getsize(output_path)//1024} KB')
                    return True
            else:
                print(f'[invidious] No suitable MP4 stream found on {instance}')
        except Exception as e:
            print(f'[invidious] Error with {instance}: {e}')
    return False

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/voices', methods=['GET'])
def get_voices():
    """Returns voices + style + age metadata."""
    return jsonify({
        'voices':       VOICES,
        'voice_styles': VOICE_STYLES,
        'mood_labels':  MOOD_LABELS,
        'age_presets':  AGE_PRESETS,
    })


@app.route('/api/preview-voice', methods=['POST'])
def preview_voice():
    """
    Generate a short voice preview sample and stream it back as audio/mpeg.
    Body: { voice, lang, style, style_degree, rate, pitch }
    """
    import tempfile
    from flask import send_file

    voice        = request.form.get('voice', 'en-US-EmmaMultilingualNeural')
    lang         = request.form.get('lang', 'en-US')
    style        = request.form.get('style', '')
    style_degree = float(request.form.get('style_degree', 1.0))
    rate         = request.form.get('rate', '+0%')
    pitch        = request.form.get('pitch', '+0Hz')

    # Write to a temp file
    tmp = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False, dir=UPLOAD_FOLDER)
    tmp.close()
    out_path = tmp.name

    ok = generate_preview(voice=voice, lang_prefix=lang,
                          style=style, style_degree=style_degree,
                          rate=rate, pitch=pitch,
                          output_path=out_path)
    if not ok or not os.path.exists(out_path):
        return jsonify({'error': 'Preview generation failed'}), 500

    return send_file(out_path, mimetype='audio/mpeg',
                     as_attachment=False,
                     download_name='preview.mp3')


@app.route('/api/clear-library', methods=['POST'])
def clear_library():
    """Delete all exported files from the outputs folder."""
    deleted, failed = [], []
    for f in os.listdir(OUTPUT_FOLDER):
        if f.endswith('.mp4') or f.endswith('.mp3'):
            try:
                os.remove(os.path.join(OUTPUT_FOLDER, f))
                deleted.append(f)
            except Exception as ex:
                failed.append({'file': f, 'error': str(ex)})
    return jsonify({'deleted': len(deleted), 'failed': failed})

@app.route('/api/merge', methods=['POST'])
def merge_videos():
    """
    Merge uploaded video clips and overlay generated/uploaded audio.
    Supports style, rate, pitch, style_degree for TTS voices.
    """
    try:
        aspect_ratio  = request.form.get('aspect_ratio', 'vertical')
        audio_source  = request.form.get('audio_source', 'script')
        language      = request.form.get('language', 'ur-PK')
        voice_id      = request.form.get('voice', 'ur-PK-UzmaNeural')
        script_text   = request.form.get('script_text', '')
        trim_audio    = request.form.get('trim_audio', 'true') == 'true'
        # Voice style / mood params
        voice_style        = request.form.get('style', '')
        voice_style_degree = float(request.form.get('style_degree', 1.0))
        voice_rate         = request.form.get('rate', '+0%')
        voice_pitch        = request.form.get('pitch', '+0Hz')
        
        # Handle video uploads
        uploaded_videos = request.files.getlist('videos')
        if not uploaded_videos or len(uploaded_videos) == 0 or uploaded_videos[0].filename == '':
            return jsonify({'success': False, 'error': 'No video files uploaded.'}), 400
            
        video_paths = []
        job_id = str(uuid.uuid4())
        
        # Save video uploads
        for idx, file in enumerate(uploaded_videos):
            filename = f"{job_id}_video_{idx}.mp4"
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            file.save(filepath)
            video_paths.append(filepath)
            
        # 1. Merge videos together
        merged_temp_video = os.path.join(UPLOAD_FOLDER, f"{job_id}_merged_raw.mp4")
        duration = concatenate_clips(video_paths, aspect_ratio=aspect_ratio, output_path=merged_temp_video)
        
        # 2. Process / Generate audio
        final_audio_path = None
        speech_path = None
        bg_music_path = None
        
        if audio_source == 'script' and script_text.strip():
            # Generate speech audio
            speech_path = os.path.join(UPLOAD_FOLDER, f"{job_id}_speech.mp3")
            generate_speech(script_text, voice_id, speech_path,
                            rate=voice_rate, pitch=voice_pitch,
                            style=voice_style, style_degree=voice_style_degree)
            
        elif audio_source == 'upload':
            uploaded_audio = request.files.get('audio_file')
            if uploaded_audio and uploaded_audio.filename != '':
                speech_path = os.path.join(UPLOAD_FOLDER, f"{job_id}_user_audio.mp3")
                uploaded_audio.save(speech_path)
                
        # Handle optional background music upload
        bg_music_file = request.files.get('bg_music_file')
        if bg_music_file and bg_music_file.filename != '':
            bg_music_path = os.path.join(UPLOAD_FOLDER, f"{job_id}_bg_music.mp3")
            bg_music_file.save(bg_music_path)
            
        # Mix audio tracks if we have speech or background music
        if speech_path or bg_music_path:
            mixed_audio = os.path.join(UPLOAD_FOLDER, f"{job_id}_mixed.mp3")
            ok = create_mixed_audio(
                voiceover_path=speech_path,
                bg_music_path=bg_music_path,
                target_duration=duration,
                output_path=mixed_audio
            )
            if ok and os.path.exists(mixed_audio):
                final_audio_path = mixed_audio
            
        # 3. Combine merged video and final audio
        output_filename = f"merged_{job_id[:8]}.mp4"
        final_output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        
        if final_audio_path and os.path.exists(final_audio_path):
            # Combine via FFmpeg for speed and precision
            cmd = [
                "ffmpeg", "-y", "-i", merged_temp_video, "-i", final_audio_path,
                "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                "-shortest" if trim_audio else "", final_output_path
            ]
            # Remove empty arguments from command
            cmd = [c for c in cmd if c != ""]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        else:
            # No audio overlay, rename raw merged video
            os.rename(merged_temp_video, final_output_path)
            
        # Cleanup temp upload files
        for p in video_paths + [merged_temp_video, speech_path, bg_music_path, final_audio_path]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
                    
        return jsonify({
            'success': True,
            'message': 'Videos merged successfully!',
            'filename': output_filename,
            'duration': f"{duration:.2f}s"
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/clip', methods=['POST'])
def clip_youtube_video():
    """
    Downloads a YouTube video, cuts it into clips, and applies anti-copyright filters.
    """
    try:
        url = request.form.get('url')
        video_file = request.files.get('video_file')
        
        if not url and (not video_file or video_file.filename == ''):
            return jsonify({'success': False, 'error': 'YouTube URL or local video file upload is required.'}), 400
            
        mode = request.form.get('mode', 'auto') # 'auto' or 'timestamps'
        interval = int(request.form.get('interval', 8))
        timestamps_str = request.form.get('timestamps', '')
        
        # Filter options
        try:
            b_width = int(request.form.get('border_width', 0))
        except ValueError:
            b_width = 0
            
        filters = {
            'aspect_ratio': request.form.get('aspect_ratio', 'original'),
            'mirror': request.form.get('mirror', 'true') == 'true',
            'zoom': request.form.get('zoom', 'true') == 'true',
            'speed': float(request.form.get('speed', 1.04)),
            'pitch_shift': float(request.form.get('pitch_shift', 0.8)),
            'border_color': request.form.get('border_color', 'none'),
            'border_width': b_width,
            'text_overlay': request.form.get('text_overlay', ''),
            'color_shift': request.form.get('color_shift', 'false') == 'true'
        }
        
        job_id = str(uuid.uuid4())
        raw_download_path = os.path.join(UPLOAD_FOLDER, f"{job_id}_raw.mp4")
        
        dl_success = False
        
        # 1. Acquire Video File (either via direct upload or YouTube download)
        if video_file and video_file.filename != '':
            print(f'[upload] Saving uploaded video file to {raw_download_path} ...')
            video_file.save(raw_download_path)
            if os.path.exists(raw_download_path) and os.path.getsize(raw_download_path) > 0:
                dl_success = True
                print('[upload] Video file successfully uploaded and verified!')
        else:
            print(f'[download] Starting YouTube download for {url}')

            # ── Method A: cobalt.tools (best — no IP restrictions) ──────────────
            dl_success = download_via_cobalt(url, raw_download_path)

        # ── Method B: pytubefix (different request path — bypasses SSL block) ─
        if not dl_success:
            print('[download] cobalt failed, trying pytubefix ...')
            dl_success = download_via_pytubefix(url, raw_download_path)

        # ── Method C: Invidious API (proxy fallback) ─────────────────────────
        if not dl_success:
            print('[download] pytubefix failed, trying Invidious ...')
            dl_success = download_via_invidious(url, raw_download_path)

        # ── Method D: yt-dlp with cookies (last resort) ──────────────────────
        if not dl_success:
            print('[download] Invidious failed, trying yt-dlp with cookies ...')
            ytdlp_bin = get_pip_binary('yt-dlp')
            try:
                import curl_cffi
                has_curl_cffi = True
            except ImportError:
                has_curl_cffi = False

            has_cookies = os.path.exists(COOKIES_FILE_PATH) and os.path.getsize(COOKIES_FILE_PATH) > 0
            yt_proxy    = os.environ.get('YT_PROXY', '')

            attempts = []
            if has_cookies:
                if has_curl_cffi:
                    attempts.append({'impersonate': 'chrome', 'player_client': 'ios,android,web',
                                     'quality': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]/best', 'timeout': 90})
                attempts.append({'impersonate': None, 'player_client': 'default,-tv',
                                 'quality': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]/best', 'timeout': 90})
            else:
                if has_curl_cffi:
                    attempts.append({'impersonate': 'chrome', 'player_client': 'ios,android,web',
                                     'quality': 'bestvideo[height<=480][ext=mp4]+bestaudio/best[height<=480]/best', 'timeout': 60})
                attempts.append({'impersonate': None, 'player_client': 'default,-tv',
                                 'quality': 'worst[ext=mp4]/worst', 'timeout': 60})

            stderr_text = ''
            for idx, attempt in enumerate(attempts, 1):
                for path in [raw_download_path, raw_download_path + '.part']:
                    if os.path.exists(path):
                        try: os.remove(path)
                        except Exception: pass

                cmd = [ytdlp_bin, '-f', attempt['quality'], '--merge-output-format', 'mp4',
                       '--geo-bypass', '--no-check-certificates', '-4',
                       '--socket-timeout', '15', '--retries', '1', '--fragment-retries', '2',
                       '-o', raw_download_path]

                if has_cookies: cmd.extend(['--cookies', COOKIES_FILE_PATH])
                if yt_proxy:    cmd.extend(['--proxy', yt_proxy])
                if attempt['impersonate']:    cmd.extend(['--impersonate', attempt['impersonate']])
                if attempt['player_client']:  cmd.extend(['--extractor-args', f"youtube:player_client={attempt['player_client']}"])
                cmd.append(url)

                try:
                    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=attempt['timeout'])
                    stderr_text = res.stderr.decode('utf-8', errors='ignore')
                    if res.returncode == 0 and os.path.exists(raw_download_path) and os.path.getsize(raw_download_path) > 0:
                        dl_success = True
                        print(f'[yt-dlp] Succeeded on attempt {idx}')
                        break
                    if any(k in stderr_text for k in ['Sign in', 'bot', 'Private', 'members-only']):
                        break
                except subprocess.TimeoutExpired:
                    stderr_text = 'Download timed out.'
                except Exception as e:
                    print(f"[yt-dlp] Attempt {idx} exception: {e}")
                    stderr_text = str(e)
                    continue

        # ── Method E: yt-dlp via Tor SOCKS5 proxy (exits from non-datacenter IPs) ──
        if not dl_success:
            print('[download] All direct methods failed, trying yt-dlp via Tor ...')
            ytdlp_bin_tor = get_pip_binary('yt-dlp')
            has_cookies_tor = os.path.exists(COOKIES_FILE_PATH) and os.path.getsize(COOKIES_FILE_PATH) > 0
            for path in [raw_download_path, raw_download_path + '.part']:
                if os.path.exists(path):
                    try: os.remove(path)
                    except Exception: pass
            tor_cmd = [
                ytdlp_bin_tor,
                '-f', 'bestvideo[height<=480][ext=mp4]+bestaudio/best[height<=480]/best',
                '--merge-output-format', 'mp4',
                '--no-check-certificates',
                '--proxy', 'socks5h://127.0.0.1:9050',  # Tor SOCKS5 (with DNS resolved on proxy)
                '--socket-timeout', '20',
                '--retries', '1',
                '-o', raw_download_path
            ]
            if has_cookies_tor:
                tor_cmd.extend(['--cookies', COOKIES_FILE_PATH])
            tor_cmd.append(url)
            try:
                tor_res = subprocess.run(tor_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
                tor_stderr = tor_res.stderr.decode('utf-8', errors='ignore')
                if tor_res.returncode == 0 and os.path.exists(raw_download_path) and os.path.getsize(raw_download_path) > 0:
                    dl_success = True
                    print('[yt-dlp/tor] Succeeded via Tor proxy!')
                else:
                    stderr_text = tor_stderr
                    print(f'[yt-dlp/tor] Failed: {tor_stderr[-200:]}')
            except subprocess.TimeoutExpired:
                stderr_text = 'Tor download timed out.'
                print('[yt-dlp/tor] Timed out.')
            except Exception as e:
                stderr_text = str(e)
                print(f'[yt-dlp/tor] Exception: {e}')

        if not dl_success:
            raw_err = stderr_text[-600:].strip() if stderr_text else 'All download methods failed'
            return jsonify({'success': False,
                            'error': f'Download failed — tried cobalt, pytubefix, invidious, yt-dlp+cookies, tor. Detail: {raw_err}'}), 500


            
        # 2. Slice downloaded video
        temp_clips_dir = os.path.join(UPLOAD_FOLDER, f"{job_id}_slices")
        os.makedirs(temp_clips_dir, exist_ok=True)
        
        custom_ranges = []
        if mode == 'timestamps' and timestamps_str:
            # Parse timestamps "10-20, 30-45"
            parts = timestamps_str.split(',')
            for part in parts:
                subparts = part.strip().split('-')
                if len(subparts) == 2:
                    try:
                        start_t = float(subparts[0].strip())
                        end_t = float(subparts[1].strip())
                        custom_ranges.append([start_t, end_t])
                    except ValueError:
                        pass
                        
        sliced_files = slice_video(
            raw_download_path,
            temp_clips_dir,
            mode=mode,
            intervals=interval,
            custom_ranges=custom_ranges
        )
        
        if not sliced_files:
            return jsonify({'success': False, 'error': 'No clips generated during slicing.'}), 500
            
        # 3. Apply safety filters to each clip and save to outputs
        processed_files = []
        for idx, file_path in enumerate(sliced_files, 1):
            out_filename = f"clip_{job_id[:8]}_{idx}.mp4"
            out_path = os.path.join(OUTPUT_FOLDER, out_filename)
            
            apply_copyright_filters(file_path, out_path, filters)
            if os.path.exists(out_path):
                processed_files.append(out_filename)
                
        # Cleanup raw downloaded file & sliced folder
        if os.path.exists(raw_download_path):
            os.remove(raw_download_path)
            
        import shutil
        if os.path.exists(temp_clips_dir):
            shutil.rmtree(temp_clips_dir)
            
        return jsonify({
            'success': True,
            'message': f'YouTube video processed into {len(processed_files)} clips!',
            'filenames': processed_files
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

def get_video_duration(path):
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            duration = float(res.stdout.strip())
            return f"{duration:.1f}s"
    except Exception:
        pass
    return "unknown"

@app.route('/api/outputs', methods=['GET'])
def get_outputs():
    """Lists all real (non-temp) output files, newest first."""
    SKIP_PATTERNS = ('.temp', 'TEMP_MPY', '.part', '.tmp')
    files = []
    for f in os.listdir(OUTPUT_FOLDER):
        # Only include clean .mp4 files — skip any temp/partial artifacts
        if not f.endswith('.mp4'):
            continue
        if any(pat in f for pat in SKIP_PATTERNS):
            # Also delete them from disk so they don't pile up
            try:
                os.remove(os.path.join(OUTPUT_FOLDER, f))
            except Exception:
                pass
            continue
        path = os.path.join(OUTPUT_FOLDER, f)
        size_mb = os.path.getsize(path) / (1024 * 1024)
        files.append({
            'filename': f,
            'size': f"{size_mb:.2f} MB",
            'duration': get_video_duration(path)
        })
    # Sort newest first
    files.sort(
        key=lambda x: os.path.getmtime(os.path.join(OUTPUT_FOLDER, x['filename'])),
        reverse=True
    )
    return jsonify(files)

@app.route('/api/generate-video', methods=['POST'])
def generate_video():
    """
    End-to-end AI script-to-video generation route.
    """
    try:
        script_text   = request.form.get('script_text', '')
        theme         = request.form.get('theme', 'auto')
        aspect_ratio  = request.form.get('aspect_ratio', 'vertical')
        voice_id      = request.form.get('voice', 'ur-PK-UzmaNeural')
        voice_rate    = request.form.get('rate', '+0%')
        voice_pitch   = request.form.get('pitch', '+0Hz')
        trim_audio    = request.form.get('trim_audio', 'true') == 'true'
        
        if not script_text.strip():
            return jsonify({'success': False, 'error': 'Script text is required.'}), 400
            
        bg_music_file = request.files.get('bg_music_file')
        
        job_id = str(uuid.uuid4())
        output_filename = f"ai_video_{job_id[:8]}.mp4"
        final_output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        
        res = generate_ai_video(
            script_text=script_text,
            theme=theme,
            aspect_ratio=aspect_ratio,
            voice_id=voice_id,
            rate=voice_rate,
            pitch=voice_pitch,
            bg_music_file=bg_music_file,
            trim_audio=trim_audio,
            output_path=final_output_path
        )
        
        if res.get('success'):
            return jsonify({
                'success': True,
                'message': 'AI Video generated successfully!',
                'filename': output_filename,
                'duration': f"{res.get('duration'):.2f}s",
                'slides': res.get('sentences_count')
            })
        else:
            return jsonify({'success': False, 'error': 'Video generation failed.'}), 500
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/outputs/<filename>', methods=['GET'])
def download_file(filename):
    """Serves file from outputs folder."""
    return send_from_directory(OUTPUT_FOLDER, filename)


if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5005))
    host = os.environ.get('HOST', '127.0.0.1')
    debug = os.environ.get('FLASK_ENV', 'production') == 'development'
    app.run(debug=debug, host=host, port=port)
