import os
import sys
import subprocess


def download_instagram(url, start=None, end=None, output="final.mp4"):
    cmd = [
        "yt-dlp",
        "-v",
        "-f", "mp4/bestvideo+bestaudio/best",
        "--merge-output-format", "mp4",
        "--retries", "5",
        "--fragment-retries", "5",
        "-o", output,
        url,
    ]

    print("Running command:", " ".join(cmd))
    subprocess.run(cmd, check=True)

    if start and end:
        trim_with_ffmpeg(output, start, end)


def trim_with_ffmpeg(path, start, end):
    trimmed = "final_trimmed.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-i", path,
        "-ss", str(start),
        "-to", str(end),
        "-c", "copy",
        trimmed,
    ]
    print("Trimming with ffmpeg:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    os.replace(trimmed, path)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python download_instagram.py <url> [start] [end]")
        sys.exit(1)

    video_url = sys.argv[1]
    start_time = sys.argv[2] if len(sys.argv) > 2 else None
    end_time = sys.argv[3] if len(sys.argv) > 3 else None

    download_instagram(video_url, start_time, end_time)
