#!/usr/bin/env python3
"""
Find hype moments in a YouTube video using Gemini AI.
Uploads clips.json to R2.
Usage: python scripts/find_clips.py <youtube_url> <job_id>
"""
import os
import sys
import json
import re
import subprocess
import google.generativeai as genai
from r2_upload import upload_json


def extract_video_id(url: str) -> str:
    """Extract YouTube video ID from various URL formats."""
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'(?:youtu\.be\/)([0-9A-Za-z_-]{11})',
        r'(?:embed\/)([0-9A-Za-z_-]{11})',
        r'(?:shorts\/)([0-9A-Za-z_-]{11})',
        r'(?:live\/)([0-9A-Za-z_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract video ID from URL: {url}")


def get_transcript(video_id: str) -> str:
    """Get transcript using yt-dlp (downloads auto-generated subtitles)."""
    print(f"[INFO] Downloading transcript for video: {video_id}")

    # Try to get auto-generated subtitles
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-auto-sub",
        "--sub-langs", "en",
        "--convert-subs", "srt",
        "--output", f"/tmp/{video_id}",
        f"https://www.youtube.com/watch?v={video_id}"
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    # Look for the subtitle file
    sub_file = f"/tmp/{video_id}.en.srt"
    if not os.path.exists(sub_file):
        # Try without language code
        for f in os.listdir("/tmp"):
            if f.startswith(video_id) and f.endswith(".srt"):
                sub_file = f"/tmp/{f}"
                break

    if not os.path.exists(sub_file):
        print(f"[WARN] No subtitles found. Using video title + description as fallback.")
        # Fallback: get title and description
        cmd_info = ["yt-dlp", "--dump-json", "--skip-download", f"https://www.youtube.com/watch?v={video_id}"]
        result = subprocess.run(cmd_info, capture_output=True, text=True)
        if result.returncode == 0:
            info = json.loads(result.stdout)
            title = info.get("title", "")
            desc = info.get("description", "")[:2000]
            return f"Title: {title}\n\nDescription: {desc}"
        raise RuntimeError("Could not get any text content from video")

    # Parse SRT to plain text
    with open(sub_file, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    # Clean up SRT format
    lines = content.split("\n")
    text_lines = []
    for line in lines:
        line = line.strip()
        # Skip timing lines and empty lines
        if not line or line.isdigit() or "-->" in line:
            continue
        text_lines.append(line)

    transcript = " ".join(text_lines)
    print(f"[INFO] Transcript length: {len(transcript)} chars")

    # Cleanup temp files
    for f in os.listdir("/tmp"):
        if f.startswith(video_id):
            os.remove(f"/tmp/{f}")

    return transcript


def find_clips_with_gemini(transcript: str, video_id: str) -> list:
    """Use Gemini to find 3 best hype moments."""
    print("[INFO] Analyzing with Gemini AI...")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("Missing GEMINI_API_KEY")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")

    prompt = f"""You are an expert content creator who finds the most viral-worthy moments in videos.

Here is a transcript from a YouTube video. Find the 3 BEST clips that would go viral on TikTok/Shorts.

Rules:
- Each clip should be 15-60 seconds long
- Pick moments with high emotion, drama, surprise, or valuable information
- Provide exact timestamps in HH:MM:SS or MM:SS format
- Give each clip a catchy title and short description

Transcript:
{transcript[:15000]}

Respond ONLY in this exact JSON format (no markdown, no explanation):
{{
  "clips": [
    {{
      "title": "Catchy title here",
      "description": "Why this clip is viral-worthy",
      "start": "00:01:23",
      "end": "00:02:15"
    }},
    ...
  ]
}}
"""

    response = model.generate_content(prompt)
    text = response.text.strip()

    # Remove markdown code blocks if present
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    data = json.loads(text)
    clips = data.get("clips", [])

    # Add video_id to each clip
    for clip in clips:
        clip["video_id"] = video_id

    print(f"[INFO] Found {len(clips)} clips")
    for i, clip in enumerate(clips):
        print(f"  [{i+1}] {clip['title']} ({clip['start']} - {clip['end']})")

    return clips


def time_to_seconds(time_str: str) -> int:
    """Convert HH:MM:SS or MM:SS to seconds."""
    parts = time_str.strip().split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    else:
        return int(parts[0])


def main():
    if len(sys.argv) < 3:
        print("Usage: python find_clips.py <youtube_url> <job_id>")
        sys.exit(1)

    youtube_url = sys.argv[1]
    job_id = sys.argv[2]

    print(f"[INFO] Job: {job_id}")
    print(f"[INFO] URL: {youtube_url}")

    try:
        video_id = extract_video_id(youtube_url)
        print(f"[INFO] Video ID: {video_id}")

        transcript = get_transcript(video_id)
        clips = find_clips_with_gemini(transcript, video_id)

        # Upload clips.json to R2
        output = {
            "job_id": job_id,
            "video_id": video_id,
            "video_url": youtube_url,
            "clips": clips
        }

        r2_key = f"jobs/{job_id}/clips.json"
        public_url = upload_json(output, r2_key)
        print(f"[SUCCESS] Uploaded clips to: {public_url}")

    except Exception as e:
        print(f"[ERROR] {e}")
        # Upload error info so dashboard knows it failed
        error_output = {
            "job_id": job_id,
            "error": str(e),
            "clips": []
        }
        try:
            upload_json(error_output, f"jobs/{job_id}/clips.json")
        except:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
