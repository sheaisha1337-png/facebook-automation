import os
import subprocess
import tempfile

def _parse_bool(val, default=False):
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes", "on")
    return bool(val)

def crop_to_aspect_ratio(clip, target_w, target_h):
    """
    Crops a video clip from its center to match the target aspect ratio,
    then resizes it to the target dimensions.
    (Kept for any remaining moviepy usage, but concatenate_clips no longer uses this.)
    """
    from moviepy.editor import VideoFileClip
    w, h = clip.size
    target_aspect = target_w / target_h
    current_aspect = w / h
    
    if current_aspect > target_aspect:
        new_w = int(h * target_aspect)
        x1 = (w - new_w) // 2
        x2 = x1 + new_w
        clip_cropped = clip.crop(x1=x1, y1=0, x2=x2, y2=h)
    else:
        new_h = int(w / target_aspect)
        y1 = (h - new_h) // 2
        y2 = y1 + new_h
        clip_cropped = clip.crop(x1=0, y1=y1, x2=w, y2=y2)
        
    return clip_cropped.resize(newsize=(target_w, target_h))

def concatenate_clips(video_paths, aspect_ratio="vertical", output_path=None):
    """
    Concatenates multiple video files using pure FFmpeg (no moviepy).
    Each clip is normalized to the target resolution before joining.
    Returns the total duration in seconds.
    """
    if not video_paths:
        raise ValueError("No video paths provided to concatenate.")

    existing = [p for p in video_paths if os.path.exists(p) and os.path.getsize(p) > 0]
    if not existing:
        raise ValueError("None of the provided video paths exist or could be loaded.")

    target_w, target_h = (540, 960) if aspect_ratio == "vertical" else (960, 540)

    # ── Step 1: Normalize every clip to target resolution ────────────
    norm_dir = tempfile.mkdtemp(prefix="concat_norm_")
    normalized = []

    for i, path in enumerate(existing):
        norm_path = os.path.join(norm_dir, f"norm_{i}.mp4")
        # scale+pad to target size, keep audio, re-encode to uniform codec
        vf = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:-1:-1:color=black,"
            f"fps=30"
        )
        cmd = [
            "ffmpeg", "-y", "-i", path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", "-filter_threads", "1", "-crf", "28",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            norm_path
        ]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode == 0 and os.path.exists(norm_path) and os.path.getsize(norm_path) > 0:
            normalized.append(norm_path)
        else:
            print(f"[concat] Warning: failed to normalize {path}, skipping.")

    if not normalized:
        raise RuntimeError("All clips failed to normalize — cannot concatenate.")

    # ── Step 2: Write concat list file ───────────────────────────────
    concat_list = os.path.join(norm_dir, "concat.txt")
    with open(concat_list, "w") as f:
        for p in normalized:
            f.write(f"file '{p}'\n")

    # ── Step 3: Get total duration via ffprobe ────────────────────────
    total_duration = 0.0
    for p in normalized:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", p],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        try:
            total_duration += float(probe.stdout.strip())
        except Exception:
            pass

    # ── Step 4: Concatenate ───────────────────────────────────────────
    if output_path:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_list,
            "-c", "copy",
            output_path
        ]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            err = r.stderr.decode("utf-8", errors="ignore")[-500:]
            raise RuntimeError(f"FFmpeg concat failed: {err}")

    # ── Cleanup temp normalized clips ─────────────────────────────────
    for p in normalized:
        try:
            os.remove(p)
        except Exception:
            pass
    try:
        os.remove(concat_list)
        os.rmdir(norm_dir)
    except Exception:
        pass

    return total_duration


def pitch_shift_audio(input_audio_path, output_audio_path, semitones=0.8):
    """
    Pitch shifts an audio file using FFmpeg's asetrate and atempo filters.
    semitones: shift amount (positive = higher, negative = lower).
    """
    multiplier = 2.0 ** (semitones / 12.0)
    sample_rate = 44100
    new_rate = int(sample_rate * multiplier)
    tempo = 1.0 / multiplier
    
    cmd = [
        "ffmpeg", "-y", "-i", input_audio_path,
        "-filter_complex", f"asetrate={new_rate},atempo={tempo}",
        output_audio_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

def apply_copyright_filters(input_path, output_path, options):
    """
    Applies visual and audio transformations to bypass copyright filters.
    Uses pure FFmpeg subprocess for reliability — no moviepy silent failures.
    """
    aspect        = options.get("aspect_ratio", "original")
    mirror_mode   = options.get("mirror_mode", "horizontal")
    color_filter  = options.get("color_filter", "cinematic")
    do_zoom       = _parse_bool(options.get("zoom"), True)
    vignette      = _parse_bool(options.get("vignette"), False)
    noise_grain   = _parse_bool(options.get("noise_grain"), False)
    text_overlay  = options.get("text_overlay", "")
    subtitle_path = options.get("subtitle_file_path")

    speed_factor  = float(options.get("speed", 1.04))
    pitch_semi    = float(options.get("pitch_shift", 0.8))

    border_color  = options.get("border_color", "none")
    try:
        border_width = int(options.get("border_width", 0))
    except ValueError:
        border_width = 0

    # ── Check if the source has an audio stream ──────────────────────
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name",
         "-of", "default=noprint_wrappers=1:nokey=1", input_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    has_audio = bool(probe.stdout.strip())

    # ── Build video filter chain ──────────────────────────────────────
    vf = []

    if aspect == "vertical":
        # Crop to 9:16 center, scale to 1080×1920
        vf.append("scale=540:960:force_original_aspect_ratio=decrease,"
                  "pad=540:960:-1:-1:color=black")
    elif aspect == "horizontal":
        vf.append("scale=960:540:force_original_aspect_ratio=decrease,"
                  "pad=960:540:-1:-1:color=black")

    if border_color != "none" and border_width > 0:
        # Pad with border color
        vf.append(f"pad=iw+2*{border_width}:ih+2*{border_width}:{border_width}:{border_width}:color={border_color}")

    # Flip Mode
    if mirror_mode == "horizontal":
        vf.append("hflip")
    elif mirror_mode == "vertical":
        vf.append("vflip")
    elif mirror_mode == "both":
        vf.append("hflip,vflip")

    if do_zoom:
        # Scale up 5%, then crop center back to original size
        vf.append("scale=iw*1.05:ih*1.05,crop=iw/1.05:ih/1.05")

    # Color Grading Filters
    if color_filter == "cinematic":
        vf.append("eq=contrast=1.15:brightness=0.03:saturation=1.2")
    elif color_filter == "vibrant":
        vf.append("eq=saturation=1.35:contrast=1.05")
    elif color_filter == "beautify":
        vf.append("eq=brightness=0.05:contrast=1.05:saturation=1.15,unsharp=3:3:0.5:3:3:0.5")
    elif color_filter == "warm":
        vf.append("colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131:0")
    elif color_filter == "cool":
        vf.append("colorchannelmixer=.3:.3:.3:0:.3:.3:.3:0:.8:.8:.8:0")
    elif color_filter == "vintage":
        vf.append("colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131:0")
    elif color_filter == "grayscale":
        vf.append("colorchannelmixer=.3:.59:.11:0:.3:.59:.11:0:.3:.59:.11:0")
    elif color_filter == "dramatic":
        vf.append("eq=contrast=1.3:brightness=-0.02:saturation=0.6")
    elif color_filter == "faded":
        vf.append("eq=contrast=0.85:brightness=0.08:saturation=0.9")

    # Vignette
    if vignette:
        vf.append("vignette=0.6283")

    # Noise/Grain
    if noise_grain:
        vf.append("noise=alls=3:allf=t")

    # Burn subtitles if present
    if subtitle_path and os.path.exists(subtitle_path):
        escaped_sub_path = subtitle_path.replace("'", "'\\''").replace(":", "\\:")
        vf.append(f"subtitles='{escaped_sub_path}':force_style='FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1'")

    # Text Overlay
    if text_overlay:
        # Check if drawtext filter is available in the host FFmpeg build
        drawtext_available = False
        try:
            filters_probe = subprocess.run(["ffmpeg", "-filters"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if "drawtext" in filters_probe.stdout:
                drawtext_available = True
        except Exception:
            pass

        if drawtext_available:
            import re
            # sanitize text to prevent filter injection issues
            clean_text = re.sub(r"[^a-zA-Z0-9\s.,!?-]", "", text_overlay)
            clean_text = clean_text.replace("'", "'\\''").replace(":", "\\:")
            
            # Resolve standard font paths for macOS/Linux compatibility
            import glob
            fontfile_param = ""
            # Search recursively in Linux standard font directory
            linux_fonts = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True) + \
                          glob.glob("/usr/share/fonts/**/*.ttc", recursive=True)
            if linux_fonts:
                escaped_p = linux_fonts[0].replace("'", "'\\''").replace(":", "\\:")
                fontfile_param = f":fontfile='{escaped_p}'"
            else:
                font_paths = [
                    "/System/Library/Fonts/Helvetica.ttc",
                    "/Library/Fonts/Arial.ttf",
                    "/System/Library/Fonts/Supplemental/Arial.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
                ]
                for p in font_paths:
                    if os.path.exists(p):
                        escaped_p = p.replace("'", "'\\''").replace(":", "\\:")
                        fontfile_param = f":fontfile='{escaped_p}'"
                        break
            vf.append(f"drawtext=text='{clean_text}'{fontfile_param}:x=(w-text_w)/2:y=100:fontsize=36:fontcolor=white:box=1:boxcolor=black@0.4:boxborderw=6")
        else:
            print("[video_effects] Warning: 'drawtext' filter is not supported by this FFmpeg build. Skipping text overlay.")

    if speed_factor != 1.0:
        vf.append(f"setpts=PTS/{speed_factor:.4f}")


    # ── Build audio filter chain ──────────────────────────────────────
    af = []

    if has_audio:
        if speed_factor != 1.0:
            af.append(f"atempo={speed_factor:.4f}")

        if pitch_semi != 0.0:
            # asetrate shifts pitch; atempo corrects back to original speed
            multiplier = 2.0 ** (pitch_semi / 12.0)
            new_rate   = int(44100 * multiplier)
            tempo_corr = 1.0 / multiplier
            af.append(f"asetrate={new_rate},atempo={tempo_corr:.6f}")

    # ── Assemble FFmpeg command ───────────────────────────────────────
    cmd = ["ffmpeg", "-y", "-i", input_path]

    if vf:
        cmd += ["-vf", ",".join(vf)]

    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", "-filter_threads", "1", "-crf", "28", "-movflags", "+faststart"]

    if has_audio:
        if af:
            cmd += ["-af", ",".join(af)]
        cmd += ["-c:a", "aac"]
    else:
        cmd += ["-an"]   # no audio stream in source — don't try to encode one

    cmd.append(output_path)

    # ── Run ───────────────────────────────────────────────────────────
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        err = result.stderr.decode("utf-8", errors="ignore")[-600:]
        raise RuntimeError(f"FFmpeg filter failed for {os.path.basename(input_path)}: {err}")


def slice_video(input_path, output_dir, mode="auto", intervals=8, custom_ranges=None):
    """
    Slices a video into multiple segments.
    mode: "auto" (split every N seconds) or "timestamps" (split by custom list of ranges).
    intervals: number of seconds per slice in auto mode.
    custom_ranges: list of tuples/lists e.g. [[10, 20], [35, 45]] (in seconds).
    Returns a list of created file paths.
    """
    duration = 0.0
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", input_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            duration = float(res.stdout.strip())
    except Exception as e:
        print(f"[slice_video] ffprobe failed to get duration: {e}")

    if duration <= 0.0:
        try:
            from moviepy.editor import VideoFileClip
            clip = VideoFileClip(input_path)
            duration = clip.duration
            clip.close()
        except Exception as e:
            print(f"[slice_video] moviepy fallback also failed: {e}")
            raise RuntimeError("Could not determine video duration.")
    
    slices = []
    if mode == "auto":
        start = 0
        idx = 1
        while start < duration:
            end = min(start + intervals, duration)
            # Avoid tiny trailing clips less than 1 second
            if duration - end < 1.0:
                end = duration
            slices.append((start, end, f"clip_{idx}.mp4"))
            idx += 1
            if end == duration:
                break
            start = end
    elif mode == "timestamps" and custom_ranges:
        for idx, r in enumerate(custom_ranges, 1):
            start = r[0]
            end = min(r[1], duration)
            if start < duration:
                slices.append((start, end, f"clip_{idx}.mp4"))
                
    output_files = []
    for start, end, filename in slices:
        out_path = os.path.join(output_dir, filename)
        # Use FFmpeg directly for fast lossless seeking and cutting
        cmd = [
            "ffmpeg", "-y", "-ss", str(start), "-to", str(end),
            "-i", input_path, "-c", "copy", out_path
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            output_files.append(out_path)
            
    return output_files
