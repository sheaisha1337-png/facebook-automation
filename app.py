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
COOKIES_FILE_PATH = os.path.join(UPLOAD_FOLDER, 'cookies.txt')
yt_cookies_content = os.environ.get('YT_COOKIES')
if yt_cookies_content:
    try:
        with open(COOKIES_FILE_PATH, 'w', encoding='utf-8') as f:
            f.write(yt_cookies_content.strip())
        print("[startup] Successfully wrote cookies.txt from YT_COOKIES environment variable.")
    except Exception as e:
        print(f"[startup] Failed to write cookies.txt: {e}")

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
                        if f.startswith('.') or f == '.gitkeep':
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

# Helper to execute shell commands (e.g. yt-dlp using local venv)
def get_pip_binary(binary_name):
    # Returns path to binary in virtual env
    venv_bin = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'venv', 'bin', binary_name)
    if os.path.exists(venv_bin):
        return venv_bin
    return binary_name

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
        if not url:
            return jsonify({'success': False, 'error': 'YouTube URL is required.'}), 400
            
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
        
        # 1. Download YouTube Video using yt-dlp
        ytdlp_bin = get_pip_binary('yt-dlp')
        
        print(f"Downloading video from {url}...")

        # Check if curl-cffi is available for --impersonate support
        try:
            import curl_cffi
            has_curl_cffi = True
        except ImportError:
            has_curl_cffi = False

        # Define download attempts with different fallback strategies
        attempts = []
        
        # Attempt 1: Impersonate chrome with ios/android/mweb/web player client (if curl_cffi is available)
        if has_curl_cffi:
            attempts.append({
                "impersonate": "chrome",
                "player_client": "ios,android,mweb,web"
            })
            
        # Attempt 2: No impersonate (standard urllib/requests), with player_client=default,-tv
        attempts.append({
            "impersonate": None,
            "player_client": "default,-tv"
        })
        
        # Attempt 3: No impersonate, standard ios/android/mweb/web player client
        attempts.append({
            "impersonate": None,
            "player_client": "ios,android,mweb,web"
        })
        
        # Attempt 4: Absolute fallback (default yt-dlp behaviour)
        attempts.append({
            "impersonate": None,
            "player_client": None
        })

        success = False
        stderr_text = ""
        
        for idx, attempt in enumerate(attempts, 1):
            print(f"Download attempt {idx}/{len(attempts)}: impersonate={attempt['impersonate']}, player_client={attempt['player_client']}")
            
            # Clean up partial download files from previous attempts
            if os.path.exists(raw_download_path):
                try:
                    os.remove(raw_download_path)
                except Exception:
                    pass
            partial_path = raw_download_path + ".part"
            if os.path.exists(partial_path):
                try:
                    os.remove(partial_path)
                except Exception:
                    pass

            cmd = [
                ytdlp_bin,
                "-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]/best",
                "--merge-output-format", "mp4",
                "--geo-bypass",
                "--no-check-certificates",
                "--socket-timeout", "40",
                "--retries", "3",
                "--fragment-retries", "5",
                "-o", raw_download_path
            ]
            
            # Use cookies if available
            if os.path.exists(COOKIES_FILE_PATH) and os.path.getsize(COOKIES_FILE_PATH) > 0:
                cmd.extend(["--cookies", COOKIES_FILE_PATH])

            # Use proxy if available
            yt_proxy = os.environ.get('YT_PROXY')
            if yt_proxy:
                cmd.extend(["--proxy", yt_proxy])

            if attempt["impersonate"]:
                cmd.extend(["--impersonate", attempt["impersonate"]])
                
            if attempt["player_client"]:
                cmd.extend(["--extractor-args", f"youtube:player_client={attempt['player_client']}"])
                
            cmd.append(url)
            
            try:
                # Run the command with 5 minutes (300s) timeout per attempt
                result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
                stderr_text = result.stderr.decode('utf-8', errors='ignore')
                
                # Check if download succeeded and file exists and has size
                if result.returncode == 0 and os.path.exists(raw_download_path) and os.path.getsize(raw_download_path) > 0:
                    success = True
                    print(f"Download succeeded on attempt {idx}!")
                    break
                else:
                    print(f"Attempt {idx} failed with return code {result.returncode}. Stderr: {stderr_text[-300:]}")
            except subprocess.TimeoutExpired:
                print(f"Attempt {idx} timed out.")
                stderr_text = "Download timed out."
                continue
            except Exception as e:
                print(f"Attempt {idx} encountered exception: {e}")
                stderr_text = str(e)
                continue

        if not success:
            # Check impersonation error FIRST before generic "unavailable"
            if 'impersonate' in stderr_text.lower() or ('curl' in stderr_text.lower() and 'cffi' in stderr_text.lower()):
                err = 'Browser impersonation library missing. Please try a different video or link.'
            elif 'SSL' in stderr_text or 'EOF' in stderr_text:
                err = 'YouTube SSL/Connection error (often caused by YouTube rate limits on hosting servers). Try again or try a different video.'
            elif 'Private' in stderr_text or 'members-only' in stderr_text:
                err = 'This video is private or members-only.'
            elif 'Sign in' in stderr_text:
                err = 'YouTube requires sign-in for this video. Try a fully public video.'
            elif 'removed' in stderr_text or 'unavailable' in stderr_text or 'not available' in stderr_text:
                err = 'This video is unavailable or has been removed from YouTube.'
            elif 'bot' in stderr_text.lower() or 'detected' in stderr_text.lower():
                err = 'YouTube detected bot activity from the hosting server. Try again in a few minutes.'
            else:
                # Show raw error so we can diagnose
                err = f'yt-dlp error: {stderr_text[-800:]}' if stderr_text else 'Unknown download error'
            return jsonify({'success': False, 'error': f'Download failed: {err}'}), 500


            
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
