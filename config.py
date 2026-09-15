import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.environ["ADMIN_IDS"].replace(" ", "").split(",") if x}
CHANNEL_ID = os.environ["CHANNEL_ID"]
CHECK_INTERVAL_MIN = int(os.getenv("CHECK_INTERVAL_MIN", "60"))
MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "5"))
ADD_SOURCE_LINK = os.getenv("ADD_SOURCE_LINK", "1") == "1"

MODEL = "claude-opus-5"
DB_PATH = os.getenv("DB_PATH", "data/bot.db")
STYLE_PATH = os.getenv("STYLE_PATH", "style.md")

# RSS-источники по Chiefs. Порядок = приоритет при дедупликации.
FEEDS = [
    ("Arrowhead Pride", "https://www.arrowheadpride.com/rss/index.xml"),
    ("Google News", "https://news.google.com/rss/search?q=%22Kansas+City+Chiefs%22&hl=en-US&gl=US&ceid=US:en"),
    ("Bing News", "https://www.bing.com/news/search?q=%22Kansas+City+Chiefs%22&format=rss"),
    ("ESPN NFL", "https://www.espn.com/espn/rss/nfl/news"),
]
