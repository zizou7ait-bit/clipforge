import os
import sys
import json
import re
import subprocess
import boto3
from botocore.client import Config

# ========== Load Environment Variables ==========
twitch_url = os.environ.get("TWITCH_URL")
job_id = os.environ.get("JOB_ID")
# Optional manual time range — if both are set, we skip AI clip-finding
# and just cut exactly this segment instead.
start_time_env = os.environ.get("START_TIME", "").strip()
end_time_env = os.environ.get("END_TIME", "").strip()

if not twitch_url or not job_id:
    print("[ERROR] Missing TWITCH_URL or JOB_ID")
    sys.exit(1)

os.makedirs("output", exist_ok=True)
json_output_path = os.path.join("output", "clips.json")
video_output_path = os.path.join("output", "clip_1.mp4")

print(f"[INFO] Processing Job: {job_id} for VOD: {twitch_url}")


# ========== Time Helpers ==========
def normalize_time(t):
    """
    Normalizes a loose time string like "1:12:5" or "72:05" into
    "HH:MM:SS(.ms)" format expected by yt-dlp / ffmpeg.
    """
    t = t.strip()
    ms = ""
    if "." in t:
        t, frac = t.split(".", 1)
        ms = "." + frac

    parts = t.split(":")
    while len(parts) < 3:
        parts.insert(0, "0")

    parts = [p.zfill(2) for p in parts]
    return ":".join(parts) + ms


def is_valid_time(t):
    t = t.strip()
    # Overall shape: H(:MM){1,2}(.fraction)? — same as before, but now
    # each MM/SS component is also range-checked (0-59) below.
    if not re.match(r"^\d{1,2}(:\d{1,2}){1,2}(\.\d+)?$", t):
        return False
    parts = t.split(".")[0].split(":")
    # Last two parts (minutes, seconds) must be strictly 0-59.
    for part in parts[1:]:
        if int(part) > 59:
            return False
    return True


def time_to_seconds(t):
    t = normalize_time(t)
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


# ========== Determine Clip Range ==========
use_manual_range = bool(start_time_env) and bool(end_time_env)

if use_manual_range:
    if not is_valid_time(start_time_env) or not is_valid_time(end_time_env):
        print(f"[ERROR] Invalid manual time format: start={start_time_env} end={end_time_env}")
        sys.exit(1)

    start_time = normalize_time(start_time_env)
    end_time = normalize_time(end_time_env)

    if time_to_seconds(start_time) >= time_to_seconds(end_time):
        print(f"[ERROR] end_time ({end_time}) must be after start_time ({start_time})")
        sys.exit(1)

    print(f"[INFO] Manual clip range requested: {start_time} - {end_time}")
    clip_title = "Manual Clip"
    clip_description = f"Manually selected segment from {start_time} to {end_time}."
else:
    # Fallback default clip (placeholder for AI-detected clip logic)
    print("[INFO] No manual time range provided — using default segment.")
    start_time = "00:00:10"
    end_time = "00:00:40"
    clip_title = "Stream Highlight"
    clip_description = "The first 30 seconds of the stream."

# Build clip data wrapped in the "clips" object matching index.php expectations
clips_data = {
    "clips": [
        {
            "title": clip_title,
            "start": start_time,
            "end": end_time,
            "description": clip_description,
            "video_file": "clip_1.mp4",
        }
    ]
}

# Save the JSON locally
with open(json_output_path, "w") as f:
    json.dump(clips_data, f, indent=4)

# ========== Download and Cut the Video using yt-dlp and ffmpeg ==========
print("[INFO] Downloading and cutting video segment...")
print(f"[INFO] Range: {start_time} -> {end_time}")

command = [
    "yt-dlp",
    "--download-sections", f"*{start_time}-{end_time}",
    "--force-keyframes-at-cuts",
    "-o", video_output_path,
]

# If a cookies file was written by the workflow (see find_clips_twitch.yml),
# pass it along. This is what gets YouTube past the "Sign in to confirm
# you're not a bot" bot-check on GitHub Actions runners. Harmless for
# Twitch URLs — yt-dlp just won't find any matching-domain cookies to use.
cookies_path = "cookies.txt"
if os.path.exists(cookies_path) and os.path.getsize(cookies_path) > 0:
    command += ["--cookies", cookies_path]
    print("[INFO] Using cookies.txt for authenticated extraction.")
else:
    print("[INFO] No cookies.txt found — proceeding without auth (YouTube may bot-check).")

command.append(twitch_url)

try:
    subprocess.run(command, check=True)
    print("[INFO] Video successfully cut and saved.")
except subprocess.CalledProcessError as e:
    print(f"[ERROR] Failed to download/cut video: {e}")
    sys.exit(1)

# ========== Upload to Cloudflare R2 ==========
print("[INFO] Uploading files to Cloudflare R2...")
s3 = boto3.client(
    's3',
    endpoint_url=f"https://{os.environ.get('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
    aws_access_key_id=os.environ.get('R2_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('R2_SECRET_ACCESS_KEY'),
    config=Config(signature_version='s3v4'),
)
bucket_name = os.environ.get('R2_BUCKET_NAME')

# Upload JSON (`clips.json`)
r2_json_key = f"jobs/{job_id}/clips.json"
s3.upload_file(json_output_path, bucket_name, r2_json_key)
print(f"[INFO] Successfully uploaded {r2_json_key}")

# Upload MP4 Video (`clip_1.mp4`)
r2_video_key = f"jobs/{job_id}/clip_1.mp4"
if os.path.exists(video_output_path):
    s3.upload_file(video_output_path, bucket_name, r2_video_key)
    print(f"[INFO] Successfully uploaded {r2_video_key}")
else:
    print("[ERROR] Video file was not found for upload.")

print("[SUCCESS] Job complete!")
