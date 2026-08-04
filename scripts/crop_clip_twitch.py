#!/usr/bin/env python3
"""
Download only a clip's time range from a Twitch VOD (not the whole VOD),
then render it in one of two vertical layouts:
  - crop:     center crop to fill 9:16
  - blur_bg:  full stream scaled to fit, centered over a blurred background
Uploads final clip to R2.

Usage:
  python scripts/crop_clip_twitch.py --job-id <id> --clip-index <i> \
      --start <t> --end <t> --vod-url <url> --layout <crop|blur_bg>
"""
import os
import sys
import argparse
import subprocess
import shutil
from r2_upload import upload_file


def to_seconds(t: str) -> float:
    parts = t.strip().split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def get_video_dimensions(path: str):
    """Return (width, height) of the input video's first video stream."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or "x" not in result.stdout:
        raise RuntimeError(f"ffprobe failed to read dimensions: {result.stderr[:300]}")
    w, h = result.stdout.strip().split("x")
    return int(w), int(h)


def detect_face_crop_x(input_path: str, sample_time: float, iw: int, crop_w: int):
    """
    Grab one representative frame from the clip and run a Haar-cascade face
    detector on it to find where the streamer's face is horizontally, then
    return the crop-window's left x-coordinate (in source pixels) so that
    window is centered on the largest detected face, clamped so it never
    runs off either edge of the frame.

    Returns None (caller should fall back to a plain center crop) if
    OpenCV isn't installed, no frame could be extracted, or no face is
    found — this is a best-effort enhancement, not a hard dependency.
    """
    try:
        import cv2
    except ImportError:
        print("[WARN] opencv-python not installed; falling back to center crop")
        return None

    frame_path = "/tmp/_face_sample_frame.jpg"
    try:
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(max(0.0, sample_time)),
            "-i", input_path,
            "-vframes", "1",
            "-q:v", "2",
            frame_path,
        ]
        subprocess.run(cmd, capture_output=True, text=True)
        if not os.path.exists(frame_path):
            print("[WARN] Could not extract sample frame for face detection")
            return None

        img = cv2.imread(frame_path)
        if img is None:
            print("[WARN] Sample frame unreadable; falling back to center crop")
            return None

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        face_cascade = cv2.CascadeClassifier(cascade_path)
        faces = face_cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)
        )

        if len(faces) == 0:
            print("[INFO] No face detected in sample frame; falling back to center crop")
            return None

        # Pick the largest detected face (most likely the streamer, not a
        # small face on a screen-share/game overlay in the background).
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        face_center_x = fx + fw / 2
        print(f"[INFO] Face detected at x={fx} y={fy} w={fw} h={fh} (frame width={img.shape[1]})")

        crop_x = int(round(face_center_x - crop_w / 2))
        crop_x = max(0, min(crop_x, iw - crop_w))
        print(f"[INFO] Face-focus crop_x set to {crop_x} (crop_w={crop_w}, source width={iw})")
        return crop_x
    finally:
        if os.path.exists(frame_path):
            os.remove(frame_path)


def crop_to_vertical(input_path: str, output_path: str, pad_offset: float,
                      start: str, end: str, layout: str = "crop"):
    """Trim off the padding and render to 9:16 using the chosen layout."""
    print(f"[INFO] Rendering to 9:16 using layout='{layout}'...")
    start_sec = to_seconds(start)
    end_sec = to_seconds(end)
    trim_in = max(0.0, start_sec - pad_offset)
    duration = end_sec - start_sec

    base_cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(trim_in),
        "-t", str(duration),
        "-i", input_path,
    ]

    if layout == "blur_bg":
        # Full stream scaled to fit width in the middle, over a blurred background
        filter_complex = (
            "[0:v]split=2[raw_bg][raw_fg];"
            "[raw_bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,gblur=sigma=30[bg];"
            "[raw_fg]scale=1080:-2:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[outv]"
        )
        video_cmd = [
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "0:a?",
        ]
    elif layout == "crop":
        # trunc(ih*9/16/2)*2 ensures an even width for yuv420p compliance
        video_cmd = [
            "-vf",
            "crop=trunc(ih*9/16/2)*2:ih:(iw-trunc(ih*9/16/2)*2)/2:0,"
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p",
            "-map", "0:v:0",
            "-map", "0:a?",
        ]
    elif layout == "face_focus":
        # Same 9:16 crop as 'crop', but the horizontal crop window is
        # shifted to center on a detected face instead of the frame's
        # geometric center. Falls back to a plain center crop if no face
        # is found (or OpenCV isn't available in this environment).
        iw, ih = get_video_dimensions(input_path)
        crop_w = (int(ih * 9 / 16) // 2) * 2  # even width, matches the 'crop' expr
        sample_time = trim_in + duration / 2
        crop_x = detect_face_crop_x(input_path, sample_time, iw, crop_w)
        crop_x_expr = str(crop_x) if crop_x is not None else "(iw-trunc(ih*9/16/2)*2)/2"

        video_cmd = [
            "-vf",
            f"crop=trunc(ih*9/16/2)*2:ih:{crop_x_expr}:0,"
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p",
            "-map", "0:v:0",
            "-map", "0:a?",
        ]
    else:
        raise ValueError(f"Unknown layout '{layout}', expected 'crop', 'blur_bg', or 'face_focus'")

    cmd = base_cmd + video_cmd + [
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] ffmpeg stderr: {result.stderr}")
        raise RuntimeError(f"ffmpeg failed: {result.stderr[:500]}")

    print(f"[INFO] Rendered video saved: {output_path}")


def download_section(vod_url: str, start: str, end: str, output_path: str) -> float:
    """
    Download only the requested time range plus a couple seconds of padding
    (yt-dlp's section cut can land slightly off a keyframe, so we pad and let
    ffmpeg trim precisely afterwards). Returns the padded start offset in seconds.
    """
    start_sec = max(0.0, to_seconds(start) - 2)
    end_sec = to_seconds(end) + 2
    section = f"*{start_sec}-{end_sec}"

    print(f"[INFO] Downloading section {section} from {vod_url}...")
    cmd = [
        "yt-dlp",
        "-f", "best[height<=1080]",
        "--download-sections", section,
        "--force-keyframes-at-cuts",
        "--merge-output-format", "mp4",
        "-o", output_path,
        vod_url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] yt-dlp stderr: {result.stderr}")
        raise RuntimeError(f"Failed to download clip section: {result.stderr[:500]}")
    print(f"[INFO] Downloaded to: {output_path}")
    return start_sec


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--clip-index", required=True, type=int)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--vod-url", required=True)
    parser.add_argument("--layout", required=False, default="crop",
                         choices=["crop", "blur_bg", "face_focus"],
                         help="Vertical layout mode: crop, blur_bg, or face_focus (default: crop)")
    args = parser.parse_args()

    print(f"[INFO] Job: {args.job_id}")
    print(f"[INFO] Clip: #{args.clip_index} ({args.start} - {args.end})")
    print(f"[INFO] VOD: {args.vod_url}")
    print(f"[INFO] Layout: {args.layout}")

    # Unique work directory per clip index to isolate concurrent workers
    work_dir = f"/tmp/{args.job_id}_clip_{args.clip_index}"
    os.makedirs(work_dir, exist_ok=True)

    section_video = f"{work_dir}/section.mp4"
    output_video = f"{work_dir}/final.mp4"

    try:
        pad_offset = download_section(args.vod_url, args.start, args.end, section_video)
        crop_to_vertical(section_video, output_video, pad_offset, args.start, args.end, args.layout)

        # NOTE: the dashboard (dashboard.php) polls R2 for a fixed key —
        # jobs/{job_id}/final.mp4 — regardless of clip index, since each job
        # currently represents a single manual clip. Keep this key in sync
        # with whatever the dashboard checks for, or it will poll forever
        # and never find the file (this was the bug: it used to be named
        # clip_{clip_index}.mp4, which the dashboard never looked for).
        r2_key = f"jobs/{args.job_id}/final.mp4"
        public_url = upload_file(output_video, r2_key)
        print(f"[SUCCESS] Uploaded final video to: {public_url}")

    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)
    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir)
            print(f"[INFO] Cleaned up {work_dir}")


if __name__ == "__main__":
    main()
