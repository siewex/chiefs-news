import os
from dotenv import load_dotenv

load_dotenv(override=True)  # .env главнее системных переменных

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.environ["ADMIN_IDS"].replace(" ", "").split(",") if x}
CHANNEL_ID = os.environ["CHANNEL_ID"]
CHECK_INTERVAL_MIN = int(os.getenv("CHECK_INTERVAL_MIN", "60"))
MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "5"))
# Быстрые источники (репосты твитов): как часто и сколько за раз
FAST_INTERVAL_MIN = int(os.getenv("FAST_INTERVAL_MIN", "10"))
FAST_MAX_PER_RUN = int(os.getenv("FAST_MAX_PER_RUN", "5"))
ADD_SOURCE_LINK = os.getenv("ADD_SOURCE_LINK", "1") == "1"

# Сторонний API (OpenAI-совместимый)
API_BASE_URL = os.getenv("API_BASE_URL", "https://ai.starimg.ru/v1")
API_KEY = os.environ["API_KEY"]
MODEL = os.getenv("MODEL", "claude-opus-4-8")

DB_PATH = os.getenv("DB_PATH", "data/bot.db")
STYLE_PATH = os.getenv("STYLE_PATH", "style.md")

# Поиск по теме для /find (Bing News RSS; Google News отдаёт редиректы, которые не раскрыть без JS)
SEARCH_FEED = "https://www.bing.com/news/search?q={q}&format=rss&mkt=en-US&setlang=en-US"

# RSS-источники по Chiefs. Порядок = приоритет при дедупликации.
FEEDS = [
    ("Arrowhead Pride", "https://www.arrowheadpride.com/rss/index.xml"),
    ("Bing News", "https://www.bing.com/news/search?q=%22Kansas+City+Chiefs%22&format=rss&mkt=en-US&setlang=en-US"),
    ("ESPN NFL", "https://www.espn.com/espn/rss/nfl/news"),
]

# Быстрые источники — куда за минуты репостят твиты инсайдеров.
# Reddit: берём только посты с пометкой источника в заголовке вида "[Schefter] ...".
REDDIT_FEEDS = [
    ("r/KansasCityChiefs", "https://www.reddit.com/r/KansasCityChiefs/new/.rss", False),
    ("r/nfl", "https://www.reddit.com/r/nfl/new/.rss", True),  # True = фильтровать по ключевым словам
]
# Bluesky-аккаунты (открытый API, ключ не нужен)
BLUESKY_ACCOUNTS = [
    "chiefs.bsky.social",
]
# Ключевые слова для общих лент (r/nfl): пост берём, если в заголовке есть хоть одно
KEYWORDS = ["chiefs", "mahomes", "kelce", "andy reid", "kansas city", "veach", "arrowhead", "spagnuolo"]
