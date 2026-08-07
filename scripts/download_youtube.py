import sys
import os
import subprocess


def download_yt(url, start_time, end_time, output_file="final.mp4"):
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4",
        "--merge-output-format", "mp4",
        "--extractor-args", "youtube:player-client=android,web",
        "--extractor-args", "youtubepot-bgutilhttp:base_url=http://127.0.0.1:4416",
        "--js-runtimes", "deno",
        "-o", output_file,
    ]

    if start_time and end_time:
        print(f"Downloading segment: {start_time} to {end_time}")
        section_arg = f"*{start_time}-{end_time}"
        cmd.extend(["--download-sections", section_arg])
        cmd.extend(["--force-keyframes-at-cuts"])
    else:
        print("No timestamps provided. Downloading full video.")

    cookies_path = "cookies.txt"
    if os.path.exists(cookies_path) and os.path.getsize(cookies_path) > 0:
        cmd.extend(["--cookies", cookies_path])
        print("Using cookies.txt for authenticated extraction.")

    cmd.append(url)

    print(f"Running command: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    url = os.environ.get("YT_URL")
    start = os.environ.get("START_TIME", "").strip()
    end = os.environ.get("END_TIME", "").strip()

    if not url:
        print("Error: YT_URL environment variable is missing.")
        sys.exit(1)

    download_yt(url, start, end)
    print("Download complete. Ready for R2 upload.")
