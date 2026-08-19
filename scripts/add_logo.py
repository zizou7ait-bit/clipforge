#!/usr/bin/env python3
"""
add_logo.py

Stamps a logo/watermark PNG onto an already-rendered vertical clip
(1080x1920, e.g. tiktok.mp4 out of tiktok-style.yml), following safe
timing/opacity rules so the mark doesn't look bot-stamped:

  - by default fades in shortly after t=0 and is removed shortly before
    the last frame, but --appear-offset 0 / --disappear-offset 0 are
    valid choices to show the logo from the very first frame and/or
    keep it up through the very last frame
  - fades in at --appear-offset seconds (0, 1.25 or 2) from the start
  - is removed --disappear-offset seconds (0, 1 or 2) before the clip ends
  - opacity is clamped to 80-90%
  - blend mode is ffmpeg's default overlay ("Normal" blend, no extra
    blend-mode filter applied)

Usage:
    python scripts/add_logo.py \
        --input tiktok.mp4 --logo logo.png --output logo.mp4 \
        --pos-x 72 --pos-y 4 --scale 16 \
        --opacity 85 --appear-offset 1.25 --disappear-offset 1
"""
import argparse
import json
import subprocess
import sys


def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def probe_dimensions(path: str):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    stream = json.loads(out.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Rendered clip to stamp (1080x1920)")
    parser.add_argument("--logo", required=True, help="Logo PNG (transparent background recommended)")
    parser.add_argument("--output", required=True)
    parser.add_argument("--pos-x", required=True, type=float, help="Logo top-left X, %% of canvas width (0-100)")
    parser.add_argument("--pos-y", required=True, type=float, help="Logo top-left Y, %% of canvas height (0-100)")
    parser.add_argument("--scale", required=False, type=float, default=16.0,
                         help="Logo width as %% of canvas width (default 16, max 100 -- as big as the user wants, up to full frame width)")
    parser.add_argument("--opacity", required=False, type=float, default=85.0,
                         help="Opacity %% (clamped to 80-90, default 85)")
    parser.add_argument("--appear-offset", required=False, default="1.25", choices=["0", "1.25", "2"],
                         help="Seconds before the logo appears at the start (0 = visible from the first frame)")
    parser.add_argument("--disappear-offset", required=False, default="1", choices=["0", "1", "2"],
                         help="Seconds before the end when the logo is removed (0 = stays visible through the last frame)")
    parser.add_argument("--canvas-width", type=int, default=None,
                         help="Override canvas width. If omitted, read from --input's actual video stream.")
    parser.add_argument("--canvas-height", type=int, default=None,
                         help="Override canvas height. If omitted, read from --input's actual video stream.")
    args = parser.parse_args()

    # Videos that went through the TikTok styling pass are always
    # 1080x1920, but Instagram reels are stamped directly on the raw
    # download (insta.php skips that styling/crop step), so they can be
    # a different resolution. Probing the real dimensions instead of
    # assuming 1080x1920 keeps the logo's x/y position on-frame either way.
    if args.canvas_width is None or args.canvas_height is None:
        probed_w, probed_h = probe_dimensions(args.input)
        if args.canvas_width is None:
            args.canvas_width = probed_w
        if args.canvas_height is None:
            args.canvas_height = probed_h
        print(f"[INFO] Using canvas {args.canvas_width}x{args.canvas_height} "
              f"(probed from {args.input})")

    duration = probe_duration(args.input)
    appear = float(args.appear_offset)
    disappear = float(args.disappear_offset)
    end = duration - disappear

    if end <= appear:
        # Very short clip -- shrink the safety margins proportionally
        # instead of failing the whole job over a timing edge case.
        print(f"[WARN] Clip duration ({duration:.2f}s) is too short for offsets "
              f"appear={appear}s / disappear={disappear}s. Scaling down.", file=sys.stderr)
        appear = min(appear, duration * 0.1)
        disappear = min(disappear, duration * 0.1)
        end = max(appear + 0.5, duration - disappear)

    opacity_pct = clamp(args.opacity, 80.0, 90.0)
    opacity_decimal = round(opacity_pct / 100.0, 3)

    logo_w = int(args.canvas_width * clamp(args.scale, 5, 100) / 100.0)
    x_px = int(args.canvas_width * clamp(args.pos_x, 0, 100) / 100.0)
    y_px = int(args.canvas_height * clamp(args.pos_y, 0, 100) / 100.0)
    # Keep the logo fully on-screen even if the dropped position lands
    # right at an edge.
    x_px = max(0, min(x_px, args.canvas_width - 10))
    y_px = max(0, min(y_px, args.canvas_height - 10))

    filter_complex = (
        f"[1:v]scale={logo_w}:-1[wm_scaled];"
        f"[wm_scaled]format=rgba,colorchannelmixer=aa={opacity_decimal}[wm];"
        f"[0:v][wm]overlay=x={x_px}:y={y_px}:"
        f"enable='between(t,{appear:.3f},{end:.3f})':format=auto[outv]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", args.input,
        "-i", args.logo,
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "copy",
        "-movflags", "+faststart", "-pix_fmt", "yuv420p",
        args.output,
    ]
    print("[INFO] " + " ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"[INFO] Wrote {args.output} "
          f"(logo visible {appear:.2f}s -> {end:.2f}s of {duration:.2f}s clip, "
          f"opacity={opacity_pct:.0f}%, pos=({x_px},{y_px})px, width={logo_w}px)")


if __name__ == "__main__":
    main()
