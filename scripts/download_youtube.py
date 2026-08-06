import sys
import os
import subprocess

def download_yt(url, start_time, end_time, output_file="final.mp4"):
    # Base yt-dlp command for best mp4 quality
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4",
        "--merge-output-format", "mp4",
        "-o", output_file
    ]

    # If times are provided, only download that specific segment to save time/bandwidth
    if start_time and end_time:
        print(f"Downloading segment: {start_time} to {end_time}")
        # yt-dlp syntax for sections: *start-end
        section_arg = f"*{start_time}-{end_time}"
        cmd.extend(["--download-sections", section_arg])
        # Force keyframe accuracy for the cut
        cmd.extend(["--force-keyframes-at-cuts"])
    else:
        print("No timestamps provided. Downloading full video.")

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
