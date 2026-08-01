
import os
import sys
import json
import subprocess
import struct
import wave
from collections import defaultdict
import boto3
from botocore.client import Config
import numpy as np

# ==================== CONFIGURATION ====================
CLIP_DURATION = 30      # seconds per clip
TOP_N_CLIPS = 3         # how many clips to extract
WINDOW_SIZE = 10        # analysis window in seconds
# =======================================================

twitch_url = os.environ.get("TWITCH_URL")
job_id = os.environ.get("JOB_ID")

if not twitch_url or not job_id:
    print("[ERROR] Missing TWITCH_URL or JOB_ID")
    sys.exit(1)

os.makedirs("output", exist_ok=True)


def seconds_to_timestamp(seconds):
    """Convert seconds to HH:MM:SS format for yt-dlp."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def find_peaks_chat_method(url):
    """
    METHOD 1: Analyze Twitch chat replay.
    Requires: pip install chat-downloader
    """
    try:
        from chat_downloader import ChatDownloader
        print("[INFO] Downloading chat replay for AI analysis...")

        chat = ChatDownloader().get_chat(url)
        message_counts = defaultdict(int)

        for message in chat:
            time_in_seconds = message.get("time_in_seconds", 0)
            window = int(time_in_seconds // WINDOW_SIZE)
            message_counts[window] += 1

        if not message_counts:
            print("[WARN] No chat messages found.")
            return None

        windows = sorted(message_counts.keys())
        counts = [message_counts[w] for w in windows]

        peaks = []
        for i in range(1, len(counts) - 1):
            # Local maxima with at least some activity
            if counts[i] > counts[i - 1] and counts[i] > counts[i + 1] and counts[i] >= 3:
                time_sec = windows[i] * WINDOW_SIZE
                peaks.append({"start_sec": time_sec, "score": counts[i]})

        peaks.sort(key=lambda x: x["score"], reverse=True)
        print(f"[INFO] Chat analysis found {len(peaks)} peak(s).")
        return peaks[:TOP_N_CLIPS]

    except ImportError:
        print("[WARN] chat-downloader not installed. Skipping chat analysis.")
        return None
    except Exception as e:
        print(f"[WARN] Chat analysis failed: {e}")
        return None


def find_peaks_audio_method(url):
    """
    METHOD 2: Analyze audio RMS energy.
    Finds loud/exciting moments. Requires: pip install numpy
    """
    print("[INFO] Falling back to audio energy analysis...")
    audio_path = "output/temp_audio.wav"

    # Download only audio (fastest quality for analysis)
    dl_cmd = [
        "yt-dlp",
        "-f", "wa",                     # worst audio = fastest download
        "--extract-audio",
        "--audio-format", "wav",
        "-o", audio_path,
        url,
    ]
    subprocess.run(dl_cmd, check=True, capture_output=True)

    with wave.open(audio_path, "rb") as wav:
        n_channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        framerate = wav.getframerate()
        n_frames = wav.getnframes()

        frames = wav.readframes(n_frames)

        if sample_width == 2:
            fmt = f"{n_frames * n_channels}h"
            samples = np.array(struct.unpack(fmt, frames), dtype=np.float32)
        else:
            raise ValueError("Unsupported sample width")

        # Stereo to mono
        if n_channels > 1:
            samples = samples.reshape(-1, n_channels).mean(axis=1)

        # Calculate RMS energy per window
        samples_per_window = framerate * WINDOW_SIZE
        energies = []

        for i in range(0, len(samples), samples_per_window):
            window = samples[i : i + samples_per_window]
            if len(window) > 0:
                rms = np.sqrt(np.mean(window**2))
                energies.append(rms)

        energies = np.array(energies)
        # Normalize 0-1
        energies = (energies - energies.min()) / (energies.max() - energies.min() + 1e-10)

        # Find local maxima above threshold
        peaks = []
        for i in range(1, len(energies) - 1):
            if energies[i] > 0.5 and energies[i] > energies[i - 1] and energies[i] > energies[i + 1]:
                time_sec = i * WINDOW_SIZE
                peaks.append({"start_sec": time_sec, "score": float(energies[i])})

        peaks.sort(key=lambda x: x["score"], reverse=True)

    # Cleanup temp audio
    if os.path.exists(audio_path):
        os.remove(audio_path)

    print(f"[INFO] Audio analysis found {len(peaks)} peak(s).")
    return peaks[:TOP_N_CLIPS]


# ==================== MAIN PIPELINE ====================
print(f"[INFO] Processing Job: {job_id} for VOD: {twitch_url}")

# 1. Detect highlights
peaks = find_peaks_chat_method(twitch_url)
if not peaks:
    peaks = find_peaks_audio_method(twitch_url)

if not peaks:
    print("[ERROR] Could not auto-detect any clip candidates.")
    sys.exit(1)

# 2. Build clips metadata
clips = []
for i, peak in enumerate(peaks, 1):
    start_sec = max(0, peak["start_sec"] - 5)  # start 5s before peak
    end_sec = start_sec + CLIP_DURATION

    start_ts = seconds_to_timestamp(start_sec)
    end_ts = seconds_to_timestamp(end_sec)
    clip_filename = f"clip_{i}.mp4"

    clips.append(
        {
            "title": f"AI Highlight #{i}",
            "start": start_ts,
            "end": end_ts,
            "description": f"Auto-detected highlight (score: {peak['score']:.2f})",
            "video_file": clip_filename,
        }
    )

clips_data = {"clips": clips}

# 3. Save JSON
json_output_path = os.path.join("output", "clips.json")
with open(json_output_path, "w") as f:
    json.dump(clips_data, f, indent=4)

print(f"[INFO] Extracting {len(clips)} clip(s)...")

# 4. Download & cut each clip
for clip in clips:
    video_output_path = os.path.join("output", clip["video_file"])
    print(f"[INFO] Cutting {clip['video_file']} | {clip['start']} → {clip['end']}")

    command = [
        "yt-dlp",
        "--download-sections", f"*{clip['start']}-{clip['end']}",
        "--force-keyframes-at-cuts",
        "-o", video_output_path,
        twitch_url,
    ]

    try:
        subprocess.run(command, check=True)
        print(f"[INFO] {clip['video_file']} ready.")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to cut {clip['video_file']}: {e}")

# 5. Upload to Cloudflare R2
print("[INFO] Uploading to Cloudflare R2...")
s3 = boto3.client(
    "s3",
    endpoint_url=f"https://{os.environ.get('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
    aws_access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
    config=Config(signature_version="s3v4"),
)
bucket_name = os.environ.get("R2_BUCKET_NAME")

# Upload JSON
r2_json_key = f"jobs/{job_id}/clips.json"
s3.upload_file(json_output_path, bucket_name, r2_json_key)
print(f"[INFO] Uploaded {r2_json_key}")

# Upload all videos
for clip in clips:
    video_path = os.path.join("output", clip["video_file"])
    if os.path.exists(video_path):
        r2_key = f"jobs/{job_id}/{clip['video_file']}"
        s3.upload_file(video_path, bucket_name, r2_key)
        print(f"[INFO] Uploaded {r2_key}")
    else:
        print(f"[ERROR] Missing file: {clip['video_file']}")

print("[SUCCESS] AI clip detection complete!")
