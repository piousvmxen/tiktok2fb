"""
TikTok -> Facebook automation bot (single page).

Paste a TikTok link into your Telegram bot chat, and this script will:
  1. Download the video without watermark (yt-dlp)
  2. Remux it (faststart) for reliable Facebook playback
  3. Upload it directly to your Facebook Page (Graph API)

Runs entirely on your local machine. No cloud hosting required.

Setup:
  1. pip install -r requirements.txt
  2. Set TELEGRAM_BOT_TOKEN, FACEBOOK_PAGE_ID, FACEBOOK_PAGE_ACCESS_TOKEN
  3. python bot.py
"""

import os
import re
import shutil
import logging
import tempfile

from pathlib import Path

import requests
import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

# ---- Configuration (set these as environment variables) ----
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
FACEBOOK_PAGE_ID = os.environ.get("FACEBOOK_PAGE_ID", "")
FACEBOOK_PAGE_ACCESS_TOKEN = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "")

TIKTOK_URL_PATTERN = re.compile(r"(https?://)?(www\.|vm\.|vt\.)?tiktok\.com/\S+")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tiktok2fb")


def download_tiktok(url: str, out_dir: Path) -> tuple[Path, str]:
    """Download a TikTok video without watermark using yt-dlp.

    Tries normal impersonated download first. If TikTok flags the video as
    requiring login (sensitive/restricted content), retries using cookies
    pulled from the local Chrome browser instead (no impersonation, since
    combining both causes TikTok to reject the request).

    Returns (video_file_path, title).
    """
    base_opts = {
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "format": "best",
        "quiet": True,
        "noplaylist": True,
    }

    attempts = [
        {**base_opts, "impersonate": ImpersonateTarget.from_str("chrome")},
        {**base_opts, "cookiesfrombrowser": ("chrome", None, None, None)},
    ]

    last_error = None
    for opts in attempts:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
            title = info.get("description") or info.get("title") or ""
            return Path(filename), title
        except yt_dlp.utils.DownloadError as e:
            last_error = e
            if "log in" not in str(e).lower():
                raise

    raise last_error





def post_to_facebook(video_path: Path, description: str = "") -> str:
    """Upload a video to a Facebook Page. Returns the new post/video ID."""
    url = f"https://graph-video.facebook.com/v19.0/{FACEBOOK_PAGE_ID}/videos"
    with open(video_path, "rb") as f:
        files = {"source": f}
        data = {
            "access_token": FACEBOOK_PAGE_ACCESS_TOKEN,
            "description": description,
        }
        resp = requests.post(url, data=data, files=files, timeout=300)
    if not resp.ok:
        raise RuntimeError(f"Facebook API error {resp.status_code}: {resp.text}")
    result = resp.json()
    if "id" not in result:
        raise RuntimeError(f"Facebook API error: {result}")
    return result["id"]


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    match = TIKTOK_URL_PATTERN.search(text)
    if not match:
        await update.message.reply_text(
            "Send me a TikTok link and I'll download it (no watermark) and post it to Facebook."
        )
        return

    tiktok_url = match.group(0)
    await update.message.reply_text("Got it — downloading...")

    tmp_dir = tempfile.mkdtemp(prefix="tiktok2fb_")
    try:
        video_path, title = download_tiktok(tiktok_url, Path(tmp_dir))
       
    except Exception as e:
        logger.exception("Download failed")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        await update.message.reply_text(f"Couldn't download that video: {e}")
        return

    # Keep a permanent copy so you can inspect/reuse it later.
    saved_dir = Path.home() / "Documents" / "tiktok2fb" / "downloads"
    saved_dir.mkdir(parents=True, exist_ok=True)
    saved_copy = saved_dir / video_path.name
    shutil.copy2(video_path, saved_copy)
    logger.info("Saved local copy: %s", saved_copy)

    await update.message.reply_text("Downloaded. Posting to Facebook...")

    try:
        post_id = post_to_facebook(video_path, description=title)
    except Exception as e:
        logger.exception("Facebook upload failed")
        await update.message.reply_text(
            f"Download worked, but posting to Facebook failed: {e}"
        )
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return

    shutil.rmtree(tmp_dir, ignore_errors=True)
    await update.message.reply_text(f"Posted to Facebook. Post ID: {post_id}")


def main():
    missing = [
        name
        for name, val in [
            ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
            ("FACEBOOK_PAGE_ID", FACEBOOK_PAGE_ID),
            ("FACEBOOK_PAGE_ACCESS_TOKEN", FACEBOOK_PAGE_ACCESS_TOKEN),
        ]
        if not val
    ]
    if missing:
        raise SystemExit(f"Missing required environment variable(s): {', '.join(missing)}")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started. Paste a TikTok link into Telegram to test it.")
    app.run_polling()


if __name__ == "__main__":
    main()
