"""Step 3c: check the labels against an independent second coder.

Usage:
    python -m src.agreement              # Claude's labels vs the keyword-rule coder
    python -m src.agreement --sheet      # (re)write labels/human_check_sheet.csv
    python -m src.agreement --human      # Claude's labels vs a filled-in human sheet

No human labels exist yet. The second coder here is a transparent keyword-rule
classifier that knows nothing about Claude's reading of each post, so agreement
between the two is a lower bound on reliability, not proof of it. The human sheet
(200 posts, stratified by episode) is there so a person can do the proper check.
"""

import argparse
import re

import pandas as pd

from src import config

STRONG_IRAN = re.compile(
    r"\b(iran\w*|tehran|khamenei|ayatollah|hormuz|irgc|fordow|natanz|esfahan|isfahan|kharg"
    r"|pezeshkian|araghchi|midnight hammer|epic fury)\b", re.IGNORECASE)

ESCALATE = re.compile(
    r"\b(strik\w*|attack\w*|hit\w*|bomb\w*|obliterat\w*|blow\w* up|destroy\w*|blockad\w*"
    r"|surrender|ultimatum|deadline|\d+ hours|kill\w*|force|retaliat\w*|too late|pay the price"
    r"|regime change|evacuate|power plants?|bridges?|shoot\w*|eliminat\w*|violat\w*"
    r"|unacceptable|finish\w* off|take the oil|hell)\b", re.IGNORECASE)

DEESCALATE = re.compile(
    r"\b(cease\s?-?fire|peace\w*|deal|talks|negotiat\w*|agreement|agreed|pause\w*|postpone\w*"
    r"|hold off|cancel\w*|extend\w*|stop shooting|open\w*|progress|productive|resolution"
    r"|end (this|the) war|wind\w* down|release\w*)\b", re.IGNORECASE)

SHEET = config.LABELS_DIR / "human_check_sheet.csv"


def rule_label(text: str) -> tuple[str, str]:
    """(about_iran, direction) from keyword counts alone."""
    if not STRONG_IRAN.search(text):
        return "no", ""
    esc, de = len(ESCALATE.findall(text)), len(DEESCALATE.findall(text))
    if esc > de:
        return "yes", "escalating"
    if de > esc:
        return "yes", "de-escalating"
    return "yes", "neutral"


def cohen_kappa(a: pd.Series, b: pd.Series) -> float:
    """Agreement between two coders beyond what chance would give (1 = perfect, 0 = chance)."""
    cats = sorted(set(a) | set(b))
    n = len(a)
    observed = (a.values == b.values).mean()
    expected = sum((a == c).mean() * (b == c).mean() for c in cats)
    return (observed - expected) / (1 - expected) if expected < 1 else 1.0


def load() -> pd.DataFrame:
    cands = pd.read_csv(config.CANDIDATES_CSV, keep_default_na=False)
    labels = pd.read_csv(config.LABELS_CSV, keep_default_na=False)
    df = cands.merge(labels, on="post_id", validate="1:1")
    df["words"] = df["text"] + "\n" + df["quoted_text"] + "\n" + df["linked_text"]
    return df


def compare(df: pd.DataFrame, other_about: str, other_dir: str, who: str) -> pd.DataFrame:
    rows = []
    k_about = cohen_kappa(df["about_iran"], df[other_about])
    rows.append({"field": "about_iran", "n": len(df), "agree": (df["about_iran"] == df[other_about]).mean(),
                 "kappa": k_about})
    both = df[(df["about_iran"] == "yes") & (df[other_about] == "yes")]
    rows.append({"field": "direction (both say about Iran)", "n": len(both),
                 "agree": (both["direction"] == both[other_dir]).mean(),
                 "kappa": cohen_kappa(both["direction"], both[other_dir])})
    out = pd.DataFrame(rows)
    print(f"\nClaude vs {who}")
    print(out.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    print("\ndirection confusion (rows = Claude, columns = " + who + "):")
    print(pd.crosstab(both["direction"], both[other_dir]).to_string())
    return out


def write_sheet(df: pd.DataFrame) -> None:
    dev = df[df["episode"] == "twelve_day_war"]
    test = df[df["episode"] == "iran_war_2026"].sample(200 - len(dev), random_state=42)
    sheet = pd.concat([dev, test]).sort_values("created_utc")
    sheet = sheet[["post_id", "episode", "created_utc", "text", "quoted_text", "linked_text"]].assign(
        about_iran="", direction="", new_info="")
    sheet.to_csv(SHEET, index=False)
    print(f"wrote {len(sheet)} posts to {SHEET.relative_to(config.ROOT)} (label columns blank)")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", action="store_true")
    ap.add_argument("--human", action="store_true")
    args = ap.parse_args(argv)
    df = load()
    if args.sheet:
        write_sheet(df)
        return
    if args.human:
        human = pd.read_csv(SHEET, keep_default_na=False)[["post_id", "about_iran", "direction"]]
        human = human[human["about_iran"] != ""].rename(columns={"about_iran": "h_about", "direction": "h_dir"})
        if human.empty:
            raise SystemExit("The human sheet has no labels yet.")
        compare(df.merge(human, on="post_id"), "h_about", "h_dir", "human")
        return
    df[["r_about", "r_dir"]] = df["words"].map(rule_label).tolist()
    out = compare(df, "r_about", "r_dir", "keyword rules")
    config.RESULTS_DIR.mkdir(exist_ok=True)
    out.to_csv(config.RESULTS_DIR / "agreement_rules.csv", index=False)


if __name__ == "__main__":
    main()
