#!/usr/bin/env python3
"""
Crop a YouTube video clip to 9:16 vertical format.
Uploads final.mp4 to R2.
Usage: python scripts/crop_clip.py --job-id <id> --clip-index <i> --start <t> --end <t> --video-id <id>
"""
import os
import sys
import argparse
import subprocess
import shutil
from r2_upload import upload_file


def download_video(video_id: str, output_path: str):
    """Download YouTube video at best quality."""
    print(f"[INFO] Downloading video {video_id}...")
    cmd = [
        "yt-dlp",
        "-f", "best[height<=1080]",  # Best quality up to 1080p
        "--merge-output-format", "mp4",
        "-o", output_path,
        f"https://www.youtube.com/watch?v={video_id}"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] yt-dlp stderr: {result.stderr}")
        raise RuntimeError(f"Failed to download video: {result.stderr[:500]}")
    print(f"[INFO] Downloaded to: {output_path}")


def crop_to_vertical(input_path: str, output_path: str, start: str, end: str):
    """Crop video to 9:16 vertical using ffmpeg."""
    print(f"[INFO] Cropping {start} to {end} in 9:16 format...")

    # Convert time to seconds for ffmpeg
    def to_seconds(t):
        parts = t.strip().split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])

    start_sec = to_seconds(start)
    end_sec = to_seconds(end)
    duration = end_sec - start_sec

    # ffmpeg: crop to 9:16 (vertical), center the crop
    # Scale to 1080x1920 (9:16) for TikTok/Reels/Shorts
    cmd = [
        "ffmpeg",
        "-y",  # Overwrite output
        "-ss", str(start_sec),
        "-t", str(duration),
        "-i", input_path,
        "-vf", "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path
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
    parser.add_argument("--video-id", required=True)
    args = parser.parse_args()

    print(f"[INFO] Job: {args.job_id}")
    print(f"[INFO] Clip: #{args.clip_index} ({args.start} - {args.end})")
    print(f"[INFO] Video: {args.video_id}")

    work_dir = f"/tmp/{args.job_id}"
    os.makedirs(work_dir, exist_ok=True)

    input_video = f"{work_dir}/source.mp4"
    output_video = f"{work_dir}/final.mp4"

    try:
        download_video(args.video_id, input_video)
        crop_to_vertical(input_video, output_video, args.start, args.end)

        r2_key = f"jobs/{args.job_id}/final.mp4"
        public_url = upload_file(output_video, r2_key)
        print(f"[SUCCESS] Uploaded final video to: {public_url}")

    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)
    finally:
        # Cleanup
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir)
            print(f"[INFO] Cleaned up {work_dir}")


if __name__ == "__main__":
    main()
