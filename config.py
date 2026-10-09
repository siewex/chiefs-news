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
def _normalize_base_url(raw: str) -> str:
    """Адрес API: дописываем https://, убираем лишний слеш в конце."""
    url = (raw or "").strip().rstrip("/")
    if not url:
        raise SystemExit("API_BASE_URL пустой — укажите адрес API в .env")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


API_BASE_URL = _normalize_base_url(os.getenv("API_BASE_URL", "https://ai.starimg.ru/v1"))
API_KEY = os.environ["API_KEY"]
MODEL = os.getenv("MODEL", "claude-sonnet-5")                 # пишет посты
FILTER_MODEL = os.getenv("FILTER_MODEL", "claude-haiku-4-5")  # решает, интересна ли новость (дёшево)
ARTICLE_MAX_CHARS = int(os.getenv("ARTICLE_MAX_CHARS", "4000"))  # сколько текста статьи отдавать модели

DB_PATH = os.getenv("DB_PATH", "data/bot.db")
STYLE_PATH = os.getenv("STYLE_PATH", "style.md")

# Поиск по теме для /find (Bing News RSS; Google News отдаёт редиректы, которые не раскрыть без JS)
SEARCH_FEED = "https://www.bing.com/news/search?q={q}&format=rss&mkt=en-US&setlang=en-US"

# RSS-источники: (подпись, адрес, фильтровать ли по ключевым словам до обращения к модели).
# True нужен общим лентам НФЛ — иначе новости о других командах занимают лимит и жгут токены.
FEEDS = [
    ("Arrowhead Pride", "https://www.arrowheadpride.com/rss/index.xml", False),
    ("Chiefs.com", "https://www.chiefs.com/rss/news", False),
    ("Arrowhead Addict", "https://arrowheadaddict.com/feed/", False),
    ("ProFootballTalk", "https://profootballtalk.nbcsports.com/feed/", True),
    ("CBS Sports NFL", "https://www.cbssports.com/rss/headlines/nfl/", True),
    ("ESPN NFL", "https://www.espn.com/espn/rss/nfl/news", True),
    # Бинг оставлен последним: он ведёт на msn.com/yahoo, откуда текст статьи почти
    # никогда не извлекается — модель видит один заголовок. Нужен как подстраховка.
    ("Bing News", "https://www.bing.com/news/search?q=%22Kansas+City+Chiefs%22&format=rss&mkt=en-US&setlang=en-US", True),
]

# Быстрые источники — куда за минуты репостят твиты инсайдеров.
# Reddit: берём только посты с пометкой источника в заголовке вида "[Schefter] ...".
# (подпись в черновике, сабреддит, фильтровать ли по ключевым словам)
REDDIT_SUBS = [
    ("r/KansasCityChiefs", "KansasCityChiefs", False),
    ("r/nfl", "nfl", True),
]
# Приложение Reddit (https://www.reddit.com/prefs/apps → create app → type: script).
# Если заполнено — работаем через их API (100 запросов в минуту, стабильно).
# Если пусто — через публичный RSS, который часто отдаёт 429 с серверных адресов.
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "python:chiefs-tg-bot:1.1 (news digest)")
# Bluesky-аккаунты (открытый API, ключ не нужен)
BLUESKY_ACCOUNTS = [
    "chiefs.bsky.social",
]
# Заголовки-пустышки: клубные промо, конкурсы, реклама партнёров. Отсекаются бесплатно,
# до обращения к модели (на chiefs.com таких половина ленты).
TITLE_BLOCKLIST = [
    "coach of the week", "presented by", "player of the week presented",
    "sweepstakes", "giveaway", "ticket giveaway", "season tickets",
    "flag football", "high school", "youth football", "cheerleader",
    "photo gallery", "wallpaper", "podcast episode", "watch party",
]

# Ключевые слова для общих лент (ESPN, Bing, r/nfl): берём, только если есть хоть одно
KEYWORDS = ["chiefs", "mahomes", "kelce", "andy reid", "kansas city", "veach", "arrowhead", "spagnuolo",
            "butker", "pacheco", "worthy", "mcduffie", "bolton", "humphrey", "karlaftis", "rice"]
