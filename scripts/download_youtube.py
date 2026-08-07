name: YouTube Downloader

on:
  workflow_dispatch:
    inputs:
      job_id:
        required: true
      yt_url:
        required: true
      start_time:
        required: false
      end_time:
        required: false

jobs:
  download:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Code
        uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Setup Deno
        uses: denoland/setup-deno@v2
        with:
          deno-version: v2.x

      - name: Install yt-dlp
        run: pip install -U yt-dlp

      - name: Write YouTube cookies
        run: |
          cat > cookies.txt << 'EOF'
          ${{ secrets.YT_COOKIES }}
          EOF

      - name: Run YouTube Download
        env:
          YT_URL: ${{ github.event.inputs.yt_url }}
          START_TIME: ${{ github.event.inputs.start_time }}
          END_TIME: ${{ github.event.inputs.end_time }}
        run: python scripts/download_youtube.py

      - name: Upload to R2 Bucket
        env:
          AWS_ACCESS_KEY_ID: ${{ secrets.R2_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_ACCESS_KEY }}
          AWS_DEFAULT_REGION: auto
          ENDPOINT_URL: https://${{ secrets.R2_ACCOUNT_ID }}.r2.cloudflarestorage.com
        run: |
          pip install awscli
          aws s3 cp final.mp4 s3://${{ secrets.R2_BUCKET_NAME }}/jobs/${{ github.event.inputs.job_id }}/final.mp4 \
            --endpoint-url $ENDPOINT_URL

      - name: Cleanup
        if: always()
        run: rm -f cookies.txt final.mp4
