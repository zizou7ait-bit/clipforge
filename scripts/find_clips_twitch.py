#!/usr/bin/env python3
"""
Find hype moments in a Twitch VOD using chat activity (message-rate z-score spikes),
then caption the best windows with Gemini using only the nearby chat as context.
Uploads clips.json to R2.
Usage: python scripts/find_clips_twitch.py <twitch_vod_url> <job_id>
"""
import os
import sys
import json
import re
import subprocess
import statistics
import google.generativeai as genai
from chat_downloader import ChatDownloader
from r2_upload import upload_json


# ---- Tunables ----
BUCKET_SECONDS = 10          # width of each time bucket for message counts
BASELINE_WINDOW = 18         # buckets (=180s) used for the rolling mean/stdev
Z_SCORE_THRESHOLD = 2.5      # stdevs above baseline that counts as a spike
MIN_UNIQUE_CHATTERS = 3      # guards against single-user spam triggering a "spike"
MERGE_GAP_SECONDS = 20       # merge spikes closer together than this into one window
REACTION_LAG_SECONDS = 5     # chat reacts a few seconds after the moment; shift clip earlier
MIN_CLIP_SECONDS = 15
MAX_CLIP_SECONDS = 60
MAX_CLIPS = 5


def extract_vod_id(url: str) -> str:
    """Extract Twitch VOD id from a videos/<id> or ?video=<id> URL."""
    match = re.search(r'(?:videos/|video=)(\d+)', url)
    if match:
        return match.group(1)
    raise ValueError(f"Could not extract VOD id from URL: {url}")


def get_vod_info(url: str):
    """Use yt-dlp to read duration + title without downloading video."""
    cmd = ["yt-dlp", "--dump-json", "--skip-download", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to read VOD metadata: {result.stderr[:500]}")
    info = json.loads(result.stdout)
    duration = float(info.get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("Could not determine VOD duration")
    return duration, info.get("title", "")


def download_chat(url: str) -> list:
    """Download the VOD's chat replay. Returns [{t, author, text}, ...]."""
    print("[INFO] Downloading chat replay...")
    messages = []
    chat = ChatDownloader().get_chat(url)
    for msg in chat:
        t = msg.get("time_in_seconds")
        if t is None or t < 0:
            continue
        author = (msg.get("author") or {}).get("name", "")
        text = msg.get("message", "") or ""
        messages.append({"t": t, "author": author, "text": text})
    print(f"[INFO] Downloaded {len(messages)} chat messages")
    return messages


def bucket_messages(messages: list, duration: float):
    """Bucket messages into fixed windows, tracking count and unique chatters per bucket."""
    n_buckets = int(duration // BUCKET_SECONDS) + 1
    counts = [0] * n_buckets
    uniques = [set() for _ in range(n_buckets)]
    for m in messages:
        idx = int(m["t"] // BUCKET_SECONDS)
        if 0 <= idx < n_buckets:
            counts[idx] += 1
            if m["author"]:
                uniques[idx].add(m["author"])
    return counts, uniques


def find_spikes(counts: list, uniques: list) -> list:
    """Rolling z-score over a trailing baseline window. Returns [(bucket_index, z), ...]."""
    spikes = []
    for i in range(BASELINE_WINDOW, len(counts)):
        window = counts[i - BASELINE_WINDOW:i]
        mean = statistics.fmean(window)
        stdev = statistics.pstdev(window) or 1.0
        z = (counts[i] - mean) / stdev
        if z >= Z_SCORE_THRESHOLD and len(uniques[i]) >= MIN_UNIQUE_CHATTERS:
            spikes.append((i, z))
    return spikes


def merge_spikes(spikes: list) -> list:
    """Merge nearby spike buckets into single windows, keeping the peak z-score."""
    if not spikes:
        return []
    windows = []
    cur_start, cur_end, cur_z = spikes[0][0], spikes[0][0], spikes[0][1]
    for idx, z in spikes[1:]:
        gap = (idx - cur_end) * BUCKET_SECONDS
        if gap <= MERGE_GAP_SECONDS:
            cur_end = idx
            cur_z = max(cur_z, z)
        else:
            windows.append((cur_start, cur_end, cur_z))
            cur_start, cur_end, cur_z = idx, idx, z
    windows.append((cur_start, cur_end, cur_z))
    return windows


def windows_to_clips(windows: list, duration: float) -> list:
    """Turn spike windows into clip start/end times, padded and lag-corrected."""
    clips = []
    for start_bucket, end_bucket, z in windows:
        peak_time = end_bucket * BUCKET_SECONDS
        start = max(0.0, peak_time - REACTION_LAG_SECONDS - 10)
        end = min(duration, start + MIN_CLIP_SECONDS + 10)
        end = min(end, start + MAX_CLIP_SECONDS)
        if end - start < MIN_CLIP_SECONDS:
            continue
        clips.append({"start_sec": start, "end_sec": end, "score": round(z, 2)})
    clips.sort(key=lambda c: c["score"], reverse=True)
    return clips[:MAX_CLIPS]


def seconds_to_hms(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def caption_clip_with_gemini(clip: dict, messages: list):
    """Ask Gemini for a title/description using only the chat around this window."""
    nearby = [
        m["text"] for m in messages
        if clip["start_sec"] - 10 <= m["t"] <= clip["end_sec"] + 10 and m["text"].strip()
    ]
    chat_sample = "\n".join(nearby[:150])

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not chat_sample.strip():
        clip["title"] = f"Hype moment ({seconds_to_hms(clip['start_sec'])})"
        clip["description"] = "Chat activity spiked here."
        return

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
    prompt = f"""Chat messages from a Twitch stream during a moment where chat activity spiked:

{chat_sample[:3000]}

Based only on this chat reaction, write a short catchy clip title (under 8 words) and a
one-sentence description of what probably just happened on stream.

Respond ONLY in this JSON format (no markdown, no explanation):
{{"title": "...", "description": "..."}}"""

    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())
        clip["title"] = data.get("title", "Hype moment")
        clip["description"] = data.get("description", "Chat activity spiked here.")
    except Exception as e:
        print(f"[WARN] Gemini captioning failed, using fallback: {e}")
        clip["title"] = f"Hype moment ({seconds_to_hms(clip['start_sec'])})"
        clip["description"] = "Chat activity spiked here."


def main():
    if len(sys.argv) < 3:
        print("Usage: python find_clips_twitch.py <twitch_vod_url> <job_id>")
        sys.exit(1)

    vod_url = sys.argv[1]
    job_id = sys.argv[2]

    print(f"[INFO] Job: {job_id}")
    print(f"[INFO] VOD: {vod_url}")

    try:
        vod_id = extract_vod_id(vod_url)
        duration, title = get_vod_info(vod_url)
        print(f"[INFO] VOD id: {vod_id}, duration: {duration:.0f}s, title: {title}")

        messages = download_chat(vod_url)
        if not messages:
            raise RuntimeError("No chat messages found (chat may be disabled or replay unavailable)")

        counts, uniques = bucket_messages(messages, duration)
        spikes = find_spikes(counts, uniques)
        windows = merge_spikes(spikes)
        clips = windows_to_clips(windows, duration)

        if not clips:
            raise RuntimeError("No chat spikes found above threshold — try lowering Z_SCORE_THRESHOLD")

        for clip in clips:
            caption_clip_with_gemini(clip, messages)
            clip["start"] = seconds_to_hms(clip["start_sec"])
            clip["end"] = seconds_to_hms(clip["end_sec"])
            clip["vod_id"] = vod_id
            clip["vod_url"] = vod_url

        print(f"[INFO] Found {len(clips)} clips")
        for i, c in enumerate(clips):
            print(f"  [{i+1}] {c['title']} ({c['start']} - {c['end']}) score={c['score']}")

        output = {
            "job_id": job_id,
            "vod_id": vod_id,
            "vod_url": vod_url,
            "platform": "twitch",
            "clips": clips,
        }

        r2_key = f"jobs/{job_id}/clips.json"
        public_url = upload_json(output, r2_key)
        print(f"[SUCCESS] Uploaded clips to: {public_url}")

    except Exception as e:
        print(f"[ERROR] {e}")
        error_output = {"job_id": job_id, "error": str(e), "clips": []}
        try:
            upload_json(error_output, f"jobs/{job_id}/clips.json")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
