#!/usr/bin/env python3
"""
Download only a clip's time range from a Twitch VOD (not the whole VOD) and
crop it to 9:16 vertical format. Uploads final.mp4 to R2.
Usage: python scripts/crop_clip_twitch.py --job-id <id> --clip-index <i> --start <t> --end <t> --vod-url <url>
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


def crop_to_vertical(input_path: str, output_path: str, pad_offset: float, start: str, end: str):
    """Trim off the padding and crop to 9:16 using ffmpeg."""
    print("[INFO] Cropping to 9:16...")
    start_sec = to_seconds(start)
    end_sec = to_seconds(end)
    trim_in = max(0.0, start_sec - pad_offset)
    duration = end_sec - start_sec

    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(trim_in),
        "-t", str(duration),
        "-i", input_path,
        "-vf", "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
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

    print(f"[INFO] Cropped video saved: {output_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--clip-index", required=True, type=int)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--vod-url", required=True)
    args = parser.parse_args()

    print(f"[INFO] Job: {args.job_id}")
    print(f"[INFO] Clip: #{args.clip_index} ({args.start} - {args.end})")
    print(f"[INFO] VOD: {args.vod_url}")

    work_dir = f"/tmp/{args.job_id}"
    os.makedirs(work_dir, exist_ok=True)

    section_video = f"{work_dir}/section.mp4"
    output_video = f"{work_dir}/final.mp4"

    try:
        pad_offset = download_section(args.vod_url, args.start, args.end, section_video)
        crop_to_vertical(section_video, output_video, pad_offset, args.start, args.end)

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
