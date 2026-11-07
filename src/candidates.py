"""Step 3a: pick the posts that might be about Iran, inside each episode's core dates.

Usage:
    python -m src.candidates          -> data/processed/candidates.csv

A post is a candidate if its text (own or quoted) matches IRAN_PATTERN. The pattern is
deliberately wide (Israel, ceasefire, nuclear...): labelling then marks each candidate
as about Iran or not, so a wide net only costs labelling time, while a narrow one would
silently lose posts. The pattern was calibrated on the Twelve-Day War by reading every
post in its core dates (see docs/decisions.md).
"""

import re

import pandas as pd

from src import config

IRAN_PATTERN = re.compile(
    r"\b(iran\w*|tehran|khamenei|ayatollah|supreme leader|hormuz|irgc|revolutionary guard"
    r"|fordow|natanz|isfahan|esfahan|pezeshkian|araghchi|persian gulf|midnight hammer"
    r"|bunker buster|b-?2s?|nuclear|enrich\w*|uranium|ceasefire|cease-fire|cease fire"
    r"|israel\w*|netanyahu|bibi|middle east|strait|houthi\w*|hezbollah|islamabad"
    r"|regime change|unconditional surrender|12 day war|twelve day war"
    # added after reading every Twelve-Day War post: these caught Iran posts the list above missed
    r"|qatar\w*|al udeid|peace\w*|wars?|oil|bomb\w*|missiles?|drones?)\b",
    re.IGNORECASE,
)

# A post that is nothing but a link to another Truth Social post.
LINK_ONLY = re.compile(r"^https://truthsocial\.com/(?:@|users/)\w+/(?:statuses/)?(\d+)\S*$")


def resolve_links(posts: pd.DataFrame) -> pd.Series:
    """For link-only posts whose target is in our own data, the target's words; else ""."""
    by_id = dict(zip(posts["truth_id"].astype(str), posts["text"] + "\n" + posts["quoted_text"]))
    def target_words(text: str) -> str:
        m = LINK_ONLY.match((text or "").strip())
        return by_id.get(m.group(1), "").strip() if m else ""
    return posts["text"].map(target_words)


def core_mask(posts: pd.DataFrame, episode: config.Episode) -> pd.Series:
    """True for posts on the episode's own dates (UTC days, inclusive)."""
    day = posts["created_utc"].dt.date
    return (day >= episode.start) & (day <= episode.end)


def matched_terms(text: str) -> str:
    return ";".join(sorted({m.group(0).lower() for m in IRAN_PATTERN.finditer(text or "")}))


def find_candidates(posts: pd.DataFrame) -> pd.DataFrame:
    posts = posts.copy()
    posts["linked_text"] = resolve_links(posts)
    out = []
    for ep in config.EPISODES.values():
        core = posts[core_mask(posts, ep)].copy()
        words = core["text"] + "\n" + core["quoted_text"] + "\n" + core["linked_text"]
        core["terms"] = words.map(matched_terms)
        out.append(core[core["terms"] != ""])
    return pd.concat(out, ignore_index=True)


def main() -> None:
    posts = pd.read_csv(config.POSTS_CSV, parse_dates=["created_utc"], keep_default_na=False,
                        dtype={"truth_id": str})
    cands = find_candidates(posts)
    cands.to_csv(config.CANDIDATES_CSV, index=False)
    print(f"{len(cands)} candidates -> {config.CANDIDATES_CSV.relative_to(config.ROOT)}")
    print(cands.groupby("episode").size().to_string())


if __name__ == "__main__":
    main()
