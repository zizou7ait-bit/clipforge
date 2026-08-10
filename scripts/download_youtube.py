import os
import sys
import subprocess
import requests


def download_via_cobalt(url, start=None, end=None, output="final.mp4"):
    base_url = os.environ.get("COBALT_API_URL")
    if not base_url:
        print("COBALT_API_URL not set; skipping Cobalt.")
        return False

    base_url = base_url.rstrip("/")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    token = os.environ.get("COBALT_AUTH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "url": url,
        "videoQuality": "1080",
        "downloadMode": "auto",
    }

    print(f"Requesting download from Cobalt instance: {base_url}")
    try:
        resp = requests.post(base_url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"Cobalt request failed: {e}")
        return False

    status = data.get("status")
    if status in ("tunnel", "redirect"):
        stream_url = data.get("url")
    elif status == "picker":
        items = data.get("picker", [])
        video_items = [i for i in items if i.get("type") == "video"] or items
        if not video_items:
            print(f"Cobalt returned an empty picker: {data}")
            return False
        stream_url = video_items[0].get("url")
    else:
        print(f"Cobalt returned an unexpected/error status: {data}")
        return False

    if not stream_url:
        print(f"Cobalt response had no stream url: {data}")
        return False

    print("Downloading resolved stream from Cobalt...")
    try:
        with requests.get(stream_url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(output, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
    except Exception as e:
        print(f"Downloading resolved Cobalt stream failed: {e}")
        return False

    if start is not None and end is not None:
        return trim_with_ffmpeg(output, start, end)

    return True


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
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"ffmpeg trim failed: {e}")
        return False
    os.replace(trimmed, path)
    return True


def download_via_ytdlp(url, start=None, end=None, output="final.mp4"):
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
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"yt-dlp failed: {e}")
        return False


def download_yt(url, start=None, end=None, output="final.mp4"):
    if download_via_cobalt(url, start, end, output):
        print("Download succeeded via Cobalt.")
        return

    print("Falling back to yt-dlp...")
    if download_via_ytdlp(url, start, end, output):
        print("Download succeeded via yt-dlp.")
        return

    raise RuntimeError("Both Cobalt and yt-dlp failed to download the video.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python download_youtube.py <url> [start] [end]")
        sys.exit(1)

    video_url = sys.argv[1]
    start_time = sys.argv[2] if len(sys.argv) > 2 else None
    end_time = sys.argv[3] if len(sys.argv) > 3 else None

    download_yt(video_url, start_time, end_time)
