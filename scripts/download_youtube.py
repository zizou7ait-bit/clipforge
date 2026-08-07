import os
import sys
import subprocess


def download_yt(url, start=None, end=None, output="final.mp4"):
    cmd = [
        "yt-dlp",
        "-v",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4",
        "--merge-output-format", "mp4",
        "--extractor-args", "youtube:player-client=ios,android,web_safari",
        "--extractor-args", "youtubepot-bgutilhttp:base_url=http://127.0.0.1:4416",
        "--js-runtimes", "deno",
        "--retries", "5",
        "--fragment-retries", "5",
    ]

    # Use cookies if provided via env var (set from a GitHub Actions secret)
    cookies_path = os.environ.get("YT_COOKIES_FILE", "cookies.txt")
    if os.path.exists(cookies_path):
        cmd += ["--cookies", cookies_path]
    else:
        print(f"Warning: cookies file not found at {cookies_path}; "
              f"continuing without cookies (more likely to hit bot detection).")

    if start is not None and end is not None:
        cmd += ["--download-sections", f"*{start}-{end}"]
    else:
        print("No timestamps provided. Downloading full video.")

    cmd += ["-o", output, url]

    print("Running command:", " ".join(cmd))
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python download_youtube.py <url> [start] [end]")
        sys.exit(1)

    video_url = sys.argv[1]
    start_time = sys.argv[2] if len(sys.argv) > 2 else None
    end_time = sys.argv[3] if len(sys.argv) > 3 else None

    download_yt(video_url, start_time, end_time)
