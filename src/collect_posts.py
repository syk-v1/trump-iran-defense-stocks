"""Step 1: download Trump's Truth Social posts around each episode and save one table.

Usage:
    python -m src.collect_posts                      # every episode in src/config.py
    python -m src.collect_posts --episode twelve_day_war
    python -m src.collect_posts --refresh            # re-download days already cached

Source: the trumpstruth.org RSS feed, which takes start_date / end_date / limit (max 500).
Things learned by probing it (see docs/decisions.md):
  * start_date and end_date are UTC calendar days, and end_date is inclusive.
  * A month can hold more than 500 posts, so we ask for one UTC day per request and
    stop with an error if a day ever comes back with exactly 500 (it would be cut off).
  * Truth Social post IDs are Mastodon-style snowflakes: id >> 16 is the creation time
    in milliseconds. We keep that as a cross-check on the feed's pubDate.

Outputs:
    data/raw/posts/YYYY-MM-DD.xml   the feed exactly as downloaded, one file per UTC day
    data/processed/posts.csv        one row per post WITH TEXT, all episodes, sorted by time
    data/processed/post_times.csv   every post's time incl. image/video-only ones (placebo step)
"""

import argparse
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd
import requests

from src import config

FEED_URL = "https://www.trumpstruth.org/feed"
FEED_LIMIT = 500
USER_AGENT = "trump-iran-defense-stocks/0.1 (student research project)"

NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "truth": "https://truthsocial.com/ns",
    "archive": "https://www.trumpstruth.org/rss-extensions",
    "media": "http://search.yahoo.com/mrss/",
}

COLUMNS = [
    "post_id", "truth_id", "created_utc", "created_utc_from_id", "post_type",
    "text", "quoted_text", "ref_type", "ref_author", "ref_truth_id", "ref_created_utc",
    "media_types", "removed", "removal_detected_utc", "url", "archive_url", "episode",
]


# ------------------------------------------------------------------ which days

def collection_days(episode: config.Episode, pad_days: int, today: date) -> list[date]:
    """Every UTC day to download for one episode: the episode padded by `pad_days`
    on each side, never later than yesterday (today's posts are still arriving)."""
    first = episode.start - timedelta(days=pad_days)
    last = (episode.end or today) + timedelta(days=pad_days)
    last = min(last, today - timedelta(days=1))
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


# ------------------------------------------------------------------ downloading

def cache_path(day: date, raw_dir: Path = config.RAW_POSTS_DIR) -> Path:
    return raw_dir / f"{day.isoformat()}.xml"


def is_settled(path: Path, day: date) -> bool:
    """True if `path` was saved at least a full day after `day` ended (UTC), so the
    archive had time to catch up. Files saved earlier get downloaded again."""
    saved = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    end_of_day = datetime.combine(day + timedelta(days=1), datetime.min.time(), timezone.utc)
    return saved >= end_of_day + timedelta(days=1)


def fetch_day(session: requests.Session, day: date, max_tries: int = 5) -> bytes:
    """Raw RSS bytes for every post on one UTC day."""
    params = {"start_date": day.isoformat(), "end_date": day.isoformat(), "limit": FEED_LIMIT}
    wait = 5.0
    for attempt in range(1, max_tries + 1):
        try:
            r = session.get(FEED_URL, params=params, timeout=60)
        except requests.RequestException as exc:
            problem = f"{type(exc).__name__}: {exc}"
        else:
            if r.status_code == 200:
                return r.content
            if r.status_code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"feed returned HTTP {r.status_code} for {day}: {r.text[:200]}")
            problem = f"HTTP {r.status_code}"
            retry_after = r.headers.get("Retry-After", "")
            if retry_after.isdigit():
                wait = max(wait, float(retry_after))
        if attempt < max_tries:
            print(f"  {day}: {problem}, retrying in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
            wait *= 2
    raise RuntimeError(f"gave up on {day} after {max_tries} tries ({problem})")


def download_days(days: list[date], refresh: bool = False, delay: float = 1.0,
                  raw_dir: Path = config.RAW_POSTS_DIR) -> list[Path]:
    """Make sure every day in `days` is cached on disk; return the cache paths.
    Waits `delay` seconds between requests to be polite to a free, volunteer-run site."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    paths, fetched = [], 0
    for day in days:
        path = cache_path(day, raw_dir)
        if path.exists() and not refresh and is_settled(path, day):
            paths.append(path)
            continue
        if fetched:
            time.sleep(delay)
        content = fetch_day(session, day)
        check_not_truncated(content, day)
        tmp = path.with_suffix(".part")      # write-then-rename: no half-written cache files
        tmp.write_bytes(content)
        tmp.replace(path)
        fetched += 1
        if fetched % 25 == 0:
            print(f"  downloaded {fetched} days (up to {day})")
        paths.append(path)
    print(f"  {fetched} days downloaded, {len(days) - fetched} read from cache")
    return paths


def check_not_truncated(content: bytes, day: date) -> None:
    n = content.count(b"<item>")
    if n >= FEED_LIMIT:
        raise RuntimeError(
            f"{day} returned {n} posts, the feed's maximum, so some are probably missing. "
            "This day needs splitting into smaller requests."
        )


# ------------------------------------------------------------------ parsing

class _PostHTML(HTMLParser):
    """Splits a post's HTML into its own paragraphs and the quoted/reposted paragraphs
    (the archive puts those inside <blockquote>)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.own: list[str] = []
        self.quoted: list[str] = []
        self._depth = 0          # how many <blockquote>s we are inside
        self._buf: list[str] = []

    def _flush(self):
        para = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        if para:
            (self.quoted if self._depth else self.own).append(para)
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "blockquote"):
            self._flush()
        if tag == "blockquote":
            # The archive labels a quote with "Quoted <name>" / "Reposted <name>" just
            # before the <blockquote>; that label is not part of the post.
            if not self._depth and self.own and re.match(r"(Quoted|Reposted) ", self.own[-1]):
                self.own.pop()
            self._depth += 1
        elif tag == "br":
            self._buf.append(" ")

    def handle_endtag(self, tag):
        if tag in ("p", "blockquote"):
            self._flush()
        if tag == "blockquote":
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data):
        self._buf.append(data)


# Paragraphs the archive adds that are not words Trump wrote.
_BOILERPLATE = re.compile(
    r"^(View (image|video|attachment)|Removed from Truth Social\. Removal detected .*)$"
)


def html_to_text(html: str) -> tuple[str, str]:
    """(own text, quoted text) as plain strings, archive boilerplate removed."""
    parser = _PostHTML()
    parser.feed(html or "")
    parser.close()
    parser._flush()
    keep = lambda paras: "\n".join(p for p in paras if not _BOILERPLATE.match(p))
    return keep(parser.own), keep(parser.quoted)


def time_from_truth_id(truth_id: str) -> pd.Timestamp | None:
    """Creation time encoded in a Truth Social ID (Mastodon snowflake: id >> 16 = ms)."""
    if not truth_id or not truth_id.isdigit():
        return None
    return pd.Timestamp(int(truth_id) >> 16, unit="ms", tz="UTC")


def _text(item: ET.Element, path: str) -> str:
    el = item.find(path, NS)
    return (el.text or "").strip() if el is not None else ""


def _utc(value: str) -> pd.Timestamp | None:
    return pd.Timestamp(value).tz_convert("UTC") if value else None


def parse_feed(content: bytes) -> list[dict]:
    """One dict per <item> in an RSS file, with the columns in COLUMNS (minus episode)."""
    root = ET.fromstring(content)
    rows = []
    for item in root.iter("item"):
        archive_url = _text(item, "link")
        truth_id = _text(item, "truth:originalId")
        post_type = _text(item, "archive:postType")
        own, quoted = html_to_text(_text(item, "description"))
        if post_type == "repost":
            # A repost has no words of its own; the archive shows "RT @user" plus a copy
            # of the reposted text. Keep that text only once, as quoted_text.
            own = ""

        ref = item.find("archive:reference", NS)
        # A repost's or quote's media sits inside <archive:reference>, not on the item.
        media = [m.get("medium", "") for m in item.findall("media:content", NS)]
        if ref is not None:
            media += [m.get("medium", "") for m in ref.findall("media:content", NS)]

        rows.append({
            "post_id": int(archive_url.rstrip("/").rsplit("/", 1)[-1]),
            "truth_id": truth_id,
            "created_utc": pd.Timestamp(parsedate_to_datetime(_text(item, "pubDate"))).tz_convert("UTC"),
            "created_utc_from_id": time_from_truth_id(truth_id),
            "post_type": post_type,
            "text": own,
            "quoted_text": quoted,
            "ref_type": ref.get("type", "") if ref is not None else "",
            "ref_author": _text(ref, "dc:creator") if ref is not None else "",
            "ref_truth_id": ref.get("originalId", "") if ref is not None else "",
            "ref_created_utc": _utc(_text(ref, "archive:publishedAt")) if ref is not None else None,
            "media_types": ";".join(sorted(set(filter(None, media)))),
            "removed": _text(item, "archive:removed") == "true",
            "removal_detected_utc": _utc(_text(item, "archive:removalDetectedAt")),
            "url": _text(item, "truth:originalUrl"),
            "archive_url": archive_url,
        })
    return rows


def episode_for(ts: pd.Timestamp, today: date) -> str:
    """Name of the episode whose padded collection window contains `ts`."""
    for ep in config.EPISODES.values():
        days = collection_days(ep, config.PAD_DAYS, today)
        if days and days[0] <= ts.date() <= days[-1]:
            return ep.name
    return ""


def build_table(paths: list[Path], today: date) -> pd.DataFrame:
    rows = [row for p in paths for row in parse_feed(p.read_bytes())]
    posts = pd.DataFrame(rows, columns=[c for c in COLUMNS if c != "episode"])
    posts = posts.drop_duplicates("post_id").sort_values("created_utc", ignore_index=True)
    posts["episode"] = posts["created_utc"].map(lambda ts: episode_for(ts, today))
    return posts[COLUMNS]


def has_text(posts: pd.DataFrame) -> pd.Series:
    """True for posts with any words in them (own or quoted). Image- or video-only
    posts are False: they can't be tagged from text, so the study leaves them out."""
    words = posts["text"].fillna("").str.strip() + posts["quoted_text"].fillna("").str.strip()
    return words != ""


# ------------------------------------------------------------------ report

def summarise(posts: pd.DataFrame, days_by_episode: dict[str, list[date]]) -> None:
    print(f"\n{len(posts)} posts saved to {config.POSTS_CSV.relative_to(config.ROOT)}")
    for name, days in days_by_episode.items():
        ep = posts[posts["episode"] == name]
        per_day = ep["created_utc"].dt.date.value_counts()
        empty = [d for d in days if d not in per_day.index]
        print(f"\n[{name}] {days[0]} to {days[-1]} ({len(days)} UTC days)")
        print(f"  posts: {len(ep)}  ({ep['post_type'].value_counts().to_dict()})")
        print(f"  removed later: {int(ep['removed'].sum())}")
        if len(per_day):
            print(f"  posts per day: median {per_day.median():.0f}, max {per_day.max()} on {per_day.idxmax()}")
        if empty:
            print(f"  days with NO posts ({len(empty)}), check for archive gaps: "
                  + ", ".join(map(str, empty[:10])) + (" ..." if len(empty) > 10 else ""))

    gap = (posts["created_utc"] - posts["created_utc_from_id"]).abs().dt.total_seconds()
    bad = posts[gap > 2]
    print(f"\npubDate vs ID timestamp: max gap {gap.max():.2f}s; {len(bad)} posts differ by >2s")
    if len(bad):
        print(bad[["post_id", "created_utc", "created_utc_from_id"]].head().to_string(index=False))


# ------------------------------------------------------------------ main

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--episode", choices=sorted(config.EPISODES), action="append",
                    help="only this episode (repeatable); default all")
    ap.add_argument("--refresh", action="store_true", help="re-download cached days")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    args = ap.parse_args(argv)

    today = datetime.now(timezone.utc).date()
    names = args.episode or list(config.EPISODES)
    days_by_episode = {}
    for name in names:
        days = collection_days(config.EPISODES[name], config.PAD_DAYS, today)
        print(f"[{name}] {days[0]} to {days[-1]}")
        download_days(days, refresh=args.refresh, delay=args.delay)
        days_by_episode[name] = days

    # Always rebuild the table from every cached day of every episode, so running one
    # episode doesn't drop the other from posts.csv.
    all_days = sorted({d for ep in config.EPISODES.values()
                       for d in collection_days(ep, config.PAD_DAYS, today)})
    paths = [cache_path(d) for d in all_days if cache_path(d).exists()]
    every_post = build_table(paths, today)
    keep = has_text(every_post)
    posts = every_post[keep].reset_index(drop=True)

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    posts.to_csv(config.POSTS_CSV, index=False)
    # Media-only posts are left out of the study, but their times are still saved:
    # the placebo step must not treat the minutes after them as "no post" times.
    every_post[["post_id", "created_utc", "episode"]].assign(has_text=keep).to_csv(
        config.POST_TIMES_CSV, index=False)
    print(f"\n{int((~keep).sum())} image/video-only posts left out of posts.csv "
          f"(their times are in {config.POST_TIMES_CSV.name})")
    summarise(posts, days_by_episode)


if __name__ == "__main__":
    main()
