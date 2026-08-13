import os
import sys
import subprocess
import requests


COBALT_DIRECTORY_URL = "https://cobalt.directory/api/working?type=api"
COBALT_USER_AGENT = "clipforge/1.0 (+https://github.com/zizou7ait-bit/clipforge)"

FALLBACK_YOUTUBE_INSTANCES = [
    "https://subito-c.meowing.de",
    "https://cobaltapi.kittycat.boo",
    "https://api-cobalt.eversiege.network",
    "https://apicobalt.mgytr.top",
    "https://api.qwkuns.me",
    "https://bergung-api.hoffnungfuerdiezukunft.net",
    "https://cobalt-omega.wolfy.love",
    "https://melon.clxxped.lol",
    "https://rue-cobalt.xenon.zone",
    "https://kitty.tame.gg",
    "https://lime.clxxped.lol",
]


def _request_cobalt(base_url, url, headers, timeout=60):
    payload = {
        "url": url,
        "videoQuality": "1080",
        "downloadMode": "auto",
    }
    try:
        resp = requests.post(base_url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  -> request to {base_url} failed: {e}")
        return None

    status = data.get("status")
    if status in ("tunnel", "redirect"):
        return data.get("url")
    elif status == "picker":
        items = data.get("picker", [])
        video_items = [i for i in items if i.get("type") == "video"] or items
        if not video_items:
            print(f"  -> {base_url} returned an empty picker: {data}")
            return None
        return video_items[0].get("url")
    else:
        print(f"  -> {base_url} returned an unexpected/error status: {data}")
        return None


def _download_stream(stream_url, output):
    print("Downloading resolved stream...")
    try:
        with requests.get(stream_url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(output, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        return True
    except Exception as e:
        print(f"  -> downloading resolved stream failed: {e}")
        return False


def download_via_cobalt(url, start=None, end=None, output="final.mp4"):
    base_url = os.environ.get("COBALT_API_URL")
    if not base_url:
        print("COBALT_API_URL not set; skipping self-hosted Cobalt.")
        return False

    base_url = base_url.rstrip("/")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    token = os.environ.get("COBALT_AUTH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    print(f"Requesting download from Cobalt instance: {base_url}")
    stream_url = _request_cobalt(base_url, url, headers)
    if not stream_url:
        return False

    if not _download_stream(stream_url, output):
        return False

    if start is not None and end is not None:
        return trim_with_ffmpeg(output, start, end)

    return True


def _proxy_headers():
    secret = os.environ.get("COBALT_PROXY_SECRET")
    return {
        "Content-Type": "application/json",
        "X-Proxy-Secret": secret or "",
    }


def _fetch_instances_via_proxy(proxy_base):
    try:
        resp = requests.get(
            proxy_base.rstrip("/") + "/directory",
            headers=_proxy_headers(),
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("youtube", [])
    except Exception as e:
        print(f"Could not fetch instance directory via proxy ({e}); using built-in fallback list instead.")
        return []


def _resolve_via_proxy(proxy_base, instance, url):
    try:
        resp = requests.post(
            proxy_base.rstrip("/") + "/resolve",
            headers=_proxy_headers(),
            json={"instance": instance, "url": url, "videoQuality": "1080", "downloadMode": "auto"},
            timeout=60,
        )
        if resp.status_code != 200:
            print(f"  -> proxy resolve via {instance} failed: HTTP {resp.status_code} {resp.text[:200]}")
            return None
        data = resp.json()
    except Exception as e:
        print(f"  -> proxy resolve via {instance} failed: {e}")
        return None

    status = data.get("status")
    if status in ("tunnel", "redirect"):
        return data.get("url")
    elif status == "picker":
        items = data.get("picker", [])
        video_items = [i for i in items if i.get("type") == "video"] or items
        return video_items[0].get("url") if video_items else None
    else:
        print(f"  -> {instance} returned an unexpected/error status via proxy: {data}")
        return None


def download_via_public_cobalt_directory(url, start=None, end=None, output="final.mp4"):
    proxy_base = os.environ.get("COBALT_PROXY_URL")
    if not proxy_base:
        print("COBALT_PROXY_URL not set; skipping proxied public Cobalt lookup.")
        return False

    print("Fetching list of public Cobalt instances that support YouTube (via proxy)...")
    instances = _fetch_instances_via_proxy(proxy_base)
    if not instances:
        instances = FALLBACK_YOUTUBE_INSTANCES

    if not instances:
        print("No working YouTube-capable instances available (live or fallback).")
        return False

    print(f"Found {len(instances)} candidate instance(s); trying them in order...")
    for base_url in instances:
        print(f"Trying public Cobalt instance (via proxy): {base_url}")
        stream_url = _resolve_via_proxy(proxy_base, base_url, url)
        if not stream_url:
            continue
        if _download_stream(stream_url, output):
            if start is not None and end is not None:
                return trim_with_ffmpeg(output, start, end)
            return True

    print("All public Cobalt instances failed.")
    return False


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
        print(f"Warning: cookies file not found at {cookies_path}; continuing without cookies.")

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
        print("Download succeeded via self-hosted Cobalt.")
        return

    print("Falling back to public Cobalt instance directory...")
    if download_via_public_cobalt_directory(url, start, end, output):
        print("Download succeeded via a public Cobalt instance.")
        return

    print("Falling back to yt-dlp...")
    if download_via_ytdlp(url, start, end, output):
        print("Download succeeded via yt-dlp.")
        return

    raise RuntimeError("Cobalt (self-hosted), public Cobalt instances, and yt-dlp all failed to download the video.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python download_youtube.py <url> [start] [end]")
        sys.exit(1)

    video_url = sys.argv[1]
    start_time = sys.argv[2] if len(sys.argv) > 2 else None
    end_time = sys.argv[3] if len(sys.argv) > 3 else None

    download_yt(video_url, start_time, end_time)
