import os
import sys
import json
import subprocess
import boto3
from botocore.client import Config

# Load Environment Variables
twitch_url = os.environ.get("TWITCH_URL")
job_id = os.environ.get("JOB_ID")

if not twitch_url or not job_id:
    print("[ERROR] Missing TWITCH_URL or JOB_ID")
    sys.exit(1)

os.makedirs("output", exist_ok=True)
json_output_path = os.path.join("output", "clips.json")
video_output_path = os.path.join("output", "clip_1.mp4")

print(f"[INFO] Processing Job: {job_id} for VOD: {twitch_url}")

# Define clip data wrapped in the "clips" object matching index.php expectations
clips_data = {
    "clips": [
        {
            "title": "Stream Highlight",
            "start": "00:00:10",
            "end": "00:00:40",
            "description": "The first 30 seconds of the stream.",
            "video_file": "clip_1.mp4"
        }
    ]
}

# Save the JSON locally
with open(json_output_path, "w") as f:
    json.dump(clips_data, f, indent=4)

# Download and Cut the Video using yt-dlp and ffmpeg
print("[INFO] Downloading and cutting video segment...")
start_time = clips_data["clips"][0]["start"]
end_time = clips_data["clips"][0]["end"]

command = [
    "yt-dlp",
    "--download-sections", f"*{start_time}-{end_time}",
    "--force-keyframes-at-cuts",
    "-o", video_output_path,
    twitch_url
]

try:
    subprocess.run(command, check=True)
    print("[INFO] Video successfully cut and saved.")
except subprocess.CalledProcessError as e:
    print(f"[ERROR] Failed to download/cut video: {e}")
    sys.exit(1)

# Upload to Cloudflare R2
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
