import asyncio
import html
import logging
import os
import re
import shutil
import tempfile
import uuid
from collections import OrderedDict
from pathlib import Path

import yt_dlp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiohttp import web

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]            # token hosting sozlamasidan olinadi
COOKIES_FILE = os.environ.get("COOKIES_FILE")  # ixtiyoriy (Instagram uchun)
MAX_SIZE = 49 * 1024 * 1024                    # Telegram bot limiti ~50 MB

# ======================================================================
#  1) EMOJILAR
#  CUSTOM ichiga premium (harakatlanuvchi) emoji ID larini yozasiz.
#  Bo'sh ("") qoldirsangiz oddiy emoji chiqadi.
#  ID ni olish: botga premium emoji yuboring, bot ID ni qaytaradi.
#  Premium emoji ishlashi uchun bot egasida Telegram Premium bo'lishi kerak.
# ======================================================================
FALLBACK = {
    "hello": "🥹", "link": "🔗", "dev": "👨‍💻",
    "fire": "🔥", "sparkle": "✨", "diamond": "💎",
    "money": "💰", "card": "💳", "cart": "🛒",
    "one": "1️⃣", "two": "2️⃣",
    "music": "🎵", "hash": "#️⃣",
}
CUSTOM = {
    "hello": "", "link": "", "dev": "",
    "fire": "", "sparkle": "", "diamond": "",
    "money": "", "card": "", "cart": "",
    "one": "", "two": "",
    "music": "", "hash": "",
}

# ======================================================================
#  2) MATNLAR  ({fire} kabi so'zlar emojiga almashadi)
# ======================================================================
START_TEXT = (
    "{hello} Assalomu aleykum, tashrifingiz uchun rahmat!\n\n"
    "{link} Men Pinterest, Instagram, YouTube, TikTok, CapCut, Facebook "
    "ijtimoiy tarmoqlaridan media yuklab beraman. "
    "Shunchaki menga havolani yuboring.\n\n"
    "{dev} Dasturchi: @mirzkbrv17"
)

HASHTAG_1_TEXT = "{fire} [#あらゆる追いかけっこを繰り広げ]"

HASHTAG_2_TEXT = (
    "{sparkle} [#TVアニメ]『古代文明の謎』 古代文明にまつわる話題のネタがついに解禁!! "
    "歴史に残された謎を一挙公開!さらに、これまで知られていなかった文化や予想外の発見に加え、"
    "誰も予想できなかった驚きの瞬間も明らかになりました!! ◆注目の古代文明ミステリーポイント"
)

PAID_TEXT = (
    "{diamond} <b>Pullik sxema</b>\n\n"
    "Assalomu aleykum, albatta bu sxema amalda sinab ko'rilgan va yillar davomidagi "
    "tajriba va ko'nikmalar asosida ko'p SMM mutaxassislari, menejerlari, kotiblari "
    "tomonidan tasdiqlangan va birga hamkorlikda ishlab chiqilgan.\n\n"
    "{one} <b>1-tarif:</b>\n"
    "Sxema: kanalni kichkinasiga kirasiz va heshteg, nastroyka hammasini olasiz. "
    "Albatta natija beradi.\n"
    "{money} <b>Narx: 35.000 so'm</b>\n\n"
    "{two} <b>2-tarif:</b>\n"
    "100% rekka chiqaradigan sxema va nastroyka, profilni yuksaltirish. "
    "Agar rekka chiqa olmasangiz, o'zimiz akkauntingizga kirib hammasini to'g'rilab, "
    "topga olib chiqib beramiz.\n"
    "{money} <b>Narx: 80.000 so'm</b>\n\n"
    "{card} <b>To'lov turi:</b> Click orqali kartaga\n\n"
    "{cart} <b>Sotib olish uchun:</b> @mirzkbrv17"
)

# Menyu tugmalari: (emoji kaliti, yozuv)
BTN_TOP1 = ("fire", "Top heshteg")
BTN_TOP2 = ("sparkle", "Top heshteg 2")
BTN_PAID = ("diamond", "Pullik sxema")

# ======================================================================
#  Yordamchi funksiyalar
# ======================================================================
URL_RE = re.compile(r"https?://\S+")
HASHTAG_RE = re.compile(r"#\w+", re.UNICODE)
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def em(key: str, custom: bool) -> str:
    cid = CUSTOM.get(key, "")
    if custom and cid:
        return f'<tg-emoji emoji-id="{cid}">{FALLBACK[key]}</tg-emoji>'
    return FALLBACK[key]


def render(template: str, custom: bool) -> str:
    for key in FALLBACK:
        template = template.replace("{" + key + "}", em(key, custom))
    return template


def btn_fields(key: str, label: str, custom: bool) -> dict:
    cid = CUSTOM.get(key, "")
    if custom and cid:
        return {"text": label, "icon_custom_emoji_id": cid}
    return {"text": f"{FALLBACK[key]} {label}"}


async def try_modes(fn):
    """Avval premium emoji bilan yuboradi; xato bersa oddiy emoji bilan qayta yuboradi."""
    modes = [True, False] if any(CUSTOM.values()) else [False]
    for i, custom in enumerate(modes):
        try:
            return await fn(custom)
        except TelegramBadRequest as e:
            if i == len(modes) - 1:
                raise
            logging.warning("Premium emoji ishlamadi, oddiy emoji bilan yuboriladi: %s", e)


def main_menu(custom: bool) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(**btn_fields(*BTN_TOP1, custom)), KeyboardButton(**btn_fields(*BTN_TOP2, custom))],
            [KeyboardButton(**btn_fields(*BTN_PAID, custom))],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def media_kb(key: str, has_video: bool, has_tags: bool, custom: bool):
    row = []
    if has_video:
        row.append(InlineKeyboardButton(**btn_fields("music", "MP3", custom), callback_data=f"aud:{key}"))
    if has_tags:
        row.append(InlineKeyboardButton(**btn_fields("hash", "Heshteg", custom), callback_data=f"tag:{key}"))
    return InlineKeyboardMarkup(inline_keyboard=[row]) if row else None


# havola -> (url, heshteglar); xotira to'lib ketmasligi uchun 500 tagacha
cache: "OrderedDict[str, tuple[str, list[str]]]" = OrderedDict()


def remember(url: str, tags: list[str]) -> str:
    key = uuid.uuid4().hex[:10]
    cache[key] = (url, tags)
    while len(cache) > 500:
        cache.popitem(last=False)
    return key


def extract_hashtags(info) -> list[str]:
    """Postning izohidagi (caption) heshteglarni yig'adi."""
    if not info:
        return []
    items = [info] + list(info.get("entries") or [])
    seen, result = set(), []
    for it in items:
        if not it:
            continue
        text = it.get("description") or it.get("title") or ""
        for tag in HASHTAG_RE.findall(text):
            if tag.lower() not in seen:
                seen.add(tag.lower())
                result.append(tag)
    return result[:40]


def download(url: str, outdir: str, audio: bool = False):
    opts = {
        "outtmpl": f"{outdir}/%(autonumber)s.%(ext)s",
        "quiet": True,
        "noprogress": True,
        "max_filesize": MAX_SIZE,
        "socket_timeout": 30,
        "retries": 3,
    }
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    if audio:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
    else:
        opts["format"] = "best[ext=mp4]/bv*+ba/best"
        opts["merge_output_format"] = "mp4"
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)


# ======================================================================
#  Botning ishlashi
# ======================================================================
dp = Dispatcher()
sem = asyncio.Semaphore(3)  # bir vaqtda 3 ta yuklash


@dp.message(CommandStart())
async def start(m: Message):
    await try_modes(lambda c: m.answer(render(START_TEXT, c), reply_markup=main_menu(c)))


@dp.message(F.text.endswith(BTN_TOP2[1]))
async def top2(m: Message):
    await try_modes(lambda c: m.answer(render(HASHTAG_2_TEXT, c)))


@dp.message(F.text.endswith(BTN_TOP1[1]))
async def top1(m: Message):
    await try_modes(lambda c: m.answer(render(HASHTAG_1_TEXT, c)))


@dp.message(F.text.endswith(BTN_PAID[1]))
async def paid(m: Message):
    await try_modes(lambda c: m.answer(render(PAID_TEXT, c)))


def _has_custom_emoji(m: Message) -> bool:
    return bool(m.entities and any(e.type == "custom_emoji" for e in m.entities))


@dp.message(_has_custom_emoji)
async def emoji_ids(m: Message):
    """Premium emoji yuborsangiz, uning ID sini qaytaradi (CUSTOM ga yozish uchun)."""
    lines = []
    for e in m.entities:
        if e.type == "custom_emoji":
            lines.append(f"{e.extract_from(m.text)}  <code>{e.custom_emoji_id}</code>")
    await m.answer("Emoji ID lar:\n" + "\n".join(lines))


@dp.message(F.text)
async def handle_link(m: Message):
    found = URL_RE.search(m.text)
    if not found:
        await m.answer("Iltimos, havola yuboring.")
        return
    url = found.group(0)
    status = await m.answer("⏳ Yuklanmoqda...")
    outdir = tempfile.mkdtemp()
    try:
        async with sem:
            info = await asyncio.to_thread(download, url, outdir)
        files = sorted(Path(outdir).iterdir())
        if not files:
            await status.edit_text("❌ Yuklab bo'lmadi (fayl juda katta yoki havola yopiq).")
            return

        tags = extract_hashtags(info)
        key = remember(url, tags)
        videos = [f for f in files if f.suffix.lower() in VIDEO_EXT]
        media = [f for f in files if f.suffix.lower() in VIDEO_EXT | IMAGE_EXT]
        last = media[-1] if media else None

        for f in media:
            is_video = f.suffix.lower() in VIDEO_EXT
            kb = (lambda c: media_kb(key, bool(videos), bool(tags), c)) if f == last else (lambda c: None)
            if is_video:
                await try_modes(
                    lambda c: m.answer_video(FSInputFile(f), supports_streaming=True, reply_markup=kb(c))
                )
            else:
                await try_modes(lambda c: m.answer_photo(FSInputFile(f), reply_markup=kb(c)))
        await status.delete()
    except Exception:
        logging.exception("download error")
        await status.edit_text("❌ Xatolik: bu havoladan yuklab bo'lmadi.")
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


@dp.callback_query(F.data.startswith("aud:"))
async def send_audio(c: CallbackQuery):
    data = cache.get(c.data.split(":", 1)[1])
    if not data:
        await c.answer("Havola eskirgan, qayta yuboring.", show_alert=True)
        return
    await c.answer("Audio tayyorlanmoqda...")
    outdir = tempfile.mkdtemp()
    try:
        async with sem:
            await asyncio.to_thread(download, data[0], outdir, True)
        mp3 = next(Path(outdir).glob("*.mp3"), None)
        if mp3:
            await c.message.answer_audio(FSInputFile(mp3))
        else:
            await c.message.answer("❌ Audio topilmadi.")
    except Exception:
        logging.exception("audio error")
        await c.message.answer("❌ Audioni olib bo'lmadi.")
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


@dp.callback_query(F.data.startswith("tag:"))
async def send_tags(c: CallbackQuery):
    data = cache.get(c.data.split(":", 1)[1])
    if not data:
        await c.answer("Havola eskirgan, qayta yuboring.", show_alert=True)
        return
    await c.answer()
    tags = data[1]
    if tags:
        # <code> ichida: bosganda hammasi bir yo'la nusxalanadi
        await c.message.answer(f"<code>{html.escape(' '.join(tags))}</code>")
    else:
        await c.message.answer("Bu postning izohida heshteg topilmadi.")


async def health(request):
    return web.Response(text="ok")


async def main():
    # Render bepul Web Service uchun port ochib turadi
    port = int(os.environ.get("PORT", 10000))
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
