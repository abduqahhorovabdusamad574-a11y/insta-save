# 🤖 Universal Video Downloader Bot

Instagram, TikTok va YouTube havolalarini yuklab, Telegram'ga yuboradigan bot
(aiogram 3 + yt-dlp + FFmpeg).

## 📁 Tuzilma

```text
video_downloader_bot/
├── downloads/          # Vaqtinchalik fayllar (avto-tozalanadi)
├── static/             # Dashboard UI (index.html, styles.css, app.js, Chart.js)
├── utils/
│   ├── __init__.py
│   ├── cookies.txt     # Instagram/YouTube cookie'lari shu yerga
│   └── helpers.py      # URL tekshiruvi, fayllarni tozalash
├── analytics.py        # Foydalanuvchi/hodisalar statistikasi (SQLite)
├── dashboard.py        # Web-dashboard serveri (aiohttp)
├── downloader.py       # yt-dlp + FFmpeg orqali yuklash (video/audio)
├── main.py             # Bot entry point (dashboard'ni avtomatik boshlaydi)
├── analytics.db        # Statistika bazi (avtomatik yaratiladi)
├── .env                # BOT_TOKEN va sozlamalar
└── requirements.txt
```

## 🚀 O'rnatish va ishga tushirish

```bash
pip install -r requirements.txt
# FFmpeg o'rnatilgan bo'lishi kerak (Windows: winget install Gyan.FFmpeg)
python main.py
```

> ⚠️ Birdaniga **bitta** bot nusxasi ishga tushirilsin — ikkita polling
> bir-birining xabarlarini "yeb" yuboradi.

## 💬 Bot bilan ishlash

| Harakat | Natija |
|---|---|
| Havola yuborish (YouTube / Instagram / TikTok) | Video yuboriladi + **2 ta tugma** |
| 🎵 **Musiqasini olish** (video ostidagi tugma) | O'sha videoning **to'liq** audiosi (mp3) |
| 📜 **Tavsif** (video ostidagi tugma) | Video muallifining izohi (YouTube description) aynan o'z matnida yuboriladi |
| `/music <havola>` | Videosidan **audio (mp3)** yuboriladi |
| `/music <qo'shiq nomi>` | YouTube'dan qidirib, **to'liq trekn**i (Shorts/preview emas) yuboradi |
| `/music` (argumentsiz) | Foydalanish bo'yicha yordam |

⏳ Yuklash davomida **soat animatsiyasi** (🕐🕑🕒… + o'tgan soniya) ko'rinib turadi.

## 📊 Dashboard (foydalanuvchilar statistikasi)

Bot ishga tushganda **http://127.0.0.1:8000** da dashboard avtomatik ochiladi
(port `DASHBOARD_PORT` bilan o'zgartiriladi).

```bash
python main.py            # bot + dashboard birga
python dashboard.py       # faqat dashboard (alohida, botdan ajratib)
```

**Nima ko'rsatiladi:**

| Bo'lim | Tafsilot |
|---|---|
| KPI kartalar | Jami foydalanuvchi, bugungi yangi/faol foydalanuvchilar, bugungi yuklashlar va xabarlar, muvaffaqiyat foizi |
| Kunlik faoliyat | 7/14/30 kun davomida yangi va faol foydalanuvchilar, yuklashlar grafigi |
| Soatlik faollik | Oxirgi 24 soat: har soatda nechta hodisa va nechta odam faol |
| Amallar taqsimoti | Video / musiqa / tavsif / /start — qaysi kamchilik ko'proq ishlatilgan |
| Jonli hodisalar | Kim, qachon, nima qildi (real vaqtda yangilanadi) |
| Foydalanuvchilar | Qidiruv, saralash, sahifalash, birinchi kirish/oxirgi faollik, CSV export |

Ma'lumotlar `analytics.db` (SQLite) da saqlanadi, dashboard **15 soniyada** bir
avto yangilanadi. Vaqt — kompyuterning **lokal vaqti**.

🔐 Kirish parolini `.env` ga qo'shsangiz dashboard parol talab qiladi:

```env
DASHBOARD_PASSWORD=juda_maxfiy_parol
```

> Tashqaridan kirish kerak bo'lsa (masalan serverda):
> `DASHBOARD_HOST=0.0.0.0` — bunga faqat `DASHBOARD_PASSWORD` bilan ruxsat bering.

## ⚙️ .env sozlamalari

| Kalit | Tasnif |
|---|---|
| `BOT_TOKEN` | @BotFather orqali olingan token (majburiy) |
| `ADMIN_CHAT_ID` | Bot egasining Telegram ID (majburiy emas) |
| `DOWNLOAD_DIR` | Yuklash papkasi (standart: `downloads`) |
| `MAX_FILE_SIZE` | Maksimal fayl hajmi, bayt (standart: 52428800 = 50 MB) |
| `AUDIO_QUALITY` | Audio sifati, kbps (standart: 192) |
| `FFMPEG_PATH` | FFmpeg binary yo'li (PATH'da bo'lmasa) |
| `COOKIES_FROM_BROWSER` | `edge` / `chrome` / `firefox` — brauzer cookie'lari (brauzer yopilgan bo'lishi kerak) |
| `DASHBOARD_HOST` | Dashboard manzili (standart: `127.0.0.1` — faqat shu kompyuter) |
| `DASHBOARD_PORT` | Dashboard porti (standart: `8000`) |
| `DASHBOARD_PASSWORD` | Dashboard kirish paroli (bo'sh = parolsiz ochiq) |
| `DASHBOARD_ENABLED` | `0` bo'lsa dashboard ishga tushmaydi |
| `ANALYTICS_DB` | Statistika bazi yo'li (standart: `analytics.db`) |

## 🔐 Instagram cookies

Instagram 2025-yildan beri ko'pchilik reel/postni **login talab qiladi**,
shuning uchun cookiesiz yuklash `LOGIN_REQUIRED` xatosini beradi.
Yechim — bir marta cookie'larni saqlash:

1. Chrome/Edge'da **instagram.com** saytiga kiring (login bo'ling).
2. [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   kengaytmasini o'rnating (yoki boshqa cookies.txt exportervi).
3. Instagram sahifasida kengaytmani bosing → **Export** → `cookies.txt` faylini saqlang.
4. Uni `utils/cookies.txt` faylining **o'rniga** qo'ying (format Netscape bo'lishi kerak).
5. Botni qayta ishga tushiring: `python main.py`

Alternativa: `.env` ichida `COOKIES_FROM_BROWSER=edge` yoqing
(brauzer yopilgan bo'lishi shart, aks holda baza qulflanadi).

Eslatma: cookie'lar **maxfiy** — hech kimga yubormang, `.gitignore` ichida.

## 🔑 Xususiyatlar

- URL faqat Instagram / YouTube / TikTok uchun qabul qilinadi.
- Telegram'ning **50 MB** limitiga moslash: avval yuqori sifat, katta bo'lsa
  sifatni pasaytirib qayta urinadi (`FILE_TOO_LARGE` xatosi).
- Yuklash `asyncio.to_thread` ichida — bot bloklanmaydi.
- Musiqa qidiruvida natijalar tekshiriladi: **45s–20 min** oralig'idagi to'liq
  trek tanlanadi (Shorts/preview emas), yuklangan mp3 manba davomiyligiga teng.
- Tavsif (description) yuklab olishsiz, faqat metadata orqali olinadi va
  4096 belgi chegarasiga mos bo'laklarda yuboriladi.
- Har bir yuklash alohida `.work_...` vaqtinchalik papkada bajariladi:
  yarim yuklangan fayllar (`.part`, `title.f137.mp4`) qolmaydi.
- Yuborilgandan keyin fayl o'chiriladi; ishga tushganda `downloads/`
  papkasidagi eski/yarim fayllar tozalanadi.
- Cookie talab qilinadigan xatolikda bot **o'zbekcha** ko'rsatma beradi.
