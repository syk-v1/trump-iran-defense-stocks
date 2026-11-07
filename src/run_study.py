"""Steps 6-7: run the event study on one episode and write its results.

Usage:
    python -m src.run_study --episode twelve_day_war     # development set
    python -m src.run_study --episode iran_war_2026      # test set (run once, settings frozen)

Writes results/<episode>_events.csv, results/<episode>_summary.csv,
results/<episode>_placebo.csv and, for 2026, results/iran_war_2026_h2.csv.
"""

import argparse
from datetime import timedelta

import numpy as np
import pandas as pd

from src import config
from src import event_study as es
from src.market_hours import NEW_YORK

BPS = 1e4


def periods_for(ep: config.Episode) -> list[tuple[str, object, object]]:
    if ep.phase_split:
        return [("phase1", ep.start, ep.phase_split - timedelta(days=1)), ("phase2", ep.phase_split, ep.end)]
    return [("all", ep.start, ep.end)]


def period_of(ts: pd.Timestamp, periods) -> str:
    d = ts.date()                                   # UTC day, same convention as the config
    for name, a, b in periods:
        if a <= d <= b:
            return name
    return ""


def load_directional(ep_name: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    c = pd.read_csv(config.CANDIDATES_CSV, parse_dates=["created_utc"], keep_default_na=False)
    lab = pd.read_csv(config.LABELS_CSV, keep_default_na=False)
    df = c.merge(lab, on="post_id")
    df = df[(df["episode"] == ep_name) & (df["about_iran"] == "yes")]
    return df[df["direction"].isin(["escalating", "de-escalating"])].copy(), df


def signed_test(events: pd.DataFrame, pools: dict, col: str, draws: int, rng) -> tuple[float, float]:
    """Mean of sign*AR over events, and the share of placebo draws at least that large,
    each event's placebo drawn from its own period's pool."""
    sign = np.where(events["direction"] == "escalating", 1.0, -1.0)
    observed = float(np.mean(sign * events[col].values))
    sims = np.zeros(draws)
    for per, g in events.groupby("period"):
        s = np.where(g["direction"] == "escalating", 1.0, -1.0)
        sims += (rng.choice(pools[per], size=(draws, len(g))) * s).sum(axis=1)
    sims /= len(events)
    return observed, float((sims >= observed).mean())


def direction_tests(ev: pd.DataFrame, pools: dict, col: str, rng, label: str) -> list[dict]:
    rows = []
    for d, side in (("escalating", "greater"), ("de-escalating", "less")):
        g = ev[ev["direction"] == d]
        if g.empty:
            continue
        obs = g[col].mean()
        sims = np.zeros(config.PLACEBO_DRAWS)
        for per, h in g.groupby("period"):
            sims += rng.choice(pools[per], size=(config.PLACEBO_DRAWS, len(h))).sum(axis=1)
        sims /= len(g)
        p = float((sims >= obs).mean() if side == "greater" else (sims <= obs).mean())
        rows.append({"sample": label, "test": f"{d} mean AR", "n": len(g),
                     "mean_bps": obs * BPS, "median_bps": g[col].median() * BPS,
                     "p_one_sided": p, "share_beyond_placebo_95": g[f"{col}_beyond"].mean()})
    if not ev.empty and ev["direction"].nunique() == 2:
        obs, p = signed_test(ev, pools, col, config.PLACEBO_DRAWS, rng)
        rows.append({"sample": label, "test": "signed mean (H1)", "n": len(ev), "mean_bps": obs * BPS,
                     "median_bps": np.nan, "p_one_sided": p, "share_beyond_placebo_95": ev[f"{col}_beyond"].mean()})
    return rows


def mark_beyond(ev: pd.DataFrame, pools: dict, col: str) -> pd.Series:
    """Escalating AR above its period's placebo 95th percentile, or de-escalating below the 5th."""
    out = []
    for _, r in ev.iterrows():
        lo, hi = np.quantile(pools[r["period"]], [0.05, 0.95])
        out.append(r[col] > hi if r["direction"] == "escalating" else r[col] < lo)
    return pd.Series(out, index=ev.index, dtype=bool)


def run(ep_name: str, k_beta: int = config.BETA_RETURN_MIN, write: bool = True) -> dict:
    ep = config.EPISODES[ep_name]
    rng = np.random.default_rng(config.SEED)
    P = es.load_prices()
    periods = periods_for(ep)

    est_days = [d for d in P.days if ep.start - timedelta(days=config.PAD_DAYS) <= d < ep.start]
    beta = es.estimate_beta(P, est_days, k_beta)
    b = beta["beta"]

    posts, iran_posts = load_directional(ep_name)
    times = pd.read_csv(config.POST_TIMES_CSV, parse_dates=["created_utc"])["created_utc"]
    post_ns = np.sort(times.dt.as_unit("ns").astype("int64").values)

    # ---------------- regular-hours events
    posts["win"] = posts["created_utc"].map(lambda s: es.regular_window(P, s))
    reg = es.cluster(posts[posts["win"].notna()], pd.Timedelta(minutes=config.CLUSTER_MINUTES))
    n_mixed = int((reg["direction"] == "mixed").sum()) if len(reg) else 0
    reg = reg[reg["direction"] != "mixed"].copy()
    reg["period"] = reg["created_utc"].map(lambda t: period_of(t, periods))
    reg["t0"] = reg["created_utc"].dt.floor("min")
    reg["t1"] = reg["t0"] + es.WINDOW
    reg["ar"] = es.abnormal(P, pd.DatetimeIndex(reg["t0"]), pd.DatetimeIndex(reg["t1"]), b)
    ri = (P.b.loc[reg["t1"]].values / P.b.loc[reg["t0"]].values - 1)
    reg["r_ita"], reg["r_spy"] = ri[:, 0], ri[:, 1]
    pre = reg["t0"].map(lambda t: es.pre_window(P, t))
    has_pre = pre.notna()
    reg["pre_ar"] = np.nan
    if has_pre.any():
        reg.loc[has_pre, "pre_ar"] = es.abnormal(
            P, pd.DatetimeIndex([w[0] for w in pre[has_pre]]), pd.DatetimeIndex([w[1] for w in pre[has_pre]]), b)

    # ---------------- placebo per period
    pools, placebo_rows = {}, []
    for name, a, z in periods:
        days = [d for d in P.days if a <= d <= z]
        starts = es.placebo_starts(P, days, post_ns, pd.Timedelta(minutes=config.PLACEBO_CLEAR_MINUTES))
        pool = es.abnormal(P, starts, starts + es.WINDOW, b)
        pools[name] = pool
        q = np.quantile(pool, [0.05, 0.5, 0.95])
        placebo_rows.append({"period": name, "trading_days": len(days), "placebo_windows": len(pool),
                             "mean_bps": pool.mean() * BPS, "sd_bps": pool.std() * BPS,
                             "p05_bps": q[0] * BPS, "p50_bps": q[1] * BPS, "p95_bps": q[2] * BPS})
    placebo = pd.DataFrame(placebo_rows)

    reg["pct_in_placebo"] = [float((pools[r.period] <= r.ar).mean()) for r in reg.itertuples()]
    reg["ar_beyond"] = mark_beyond(reg, pools, "ar")
    reg["pre_ar_beyond"] = False
    if has_pre.any():
        reg.loc[has_pre, "pre_ar_beyond"] = mark_beyond(reg[has_pre], pools, "pre_ar")

    rows = []
    rows += direction_tests(reg, pools, "ar", rng, "regular hours, all")
    rows += direction_tests(reg[reg["new"]], pools, "ar", rng, "regular hours, new info")
    for name, *_ in periods if len(periods) > 1 else []:
        rows += direction_tests(reg[reg["period"] == name], pools, "ar", rng, f"regular hours, {name}")
    pre_ev = reg[reg["pre_ar"].notna()]
    rows += direction_tests(pre_ev.assign(ar=pre_ev["pre_ar"], ar_beyond=pre_ev["pre_ar_beyond"]),
                            pools, "ar", rng, "30 min BEFORE post")

    # ---------------- off-hours events (next open)
    off = posts[posts["win"].isna()].copy()
    off["key"] = off["created_utc"].map(lambda s: es.overnight_key(P, s))
    off = off[off["key"].notna()]
    ov = es.cluster(off, pd.Timedelta(0), key="key") if len(off) else pd.DataFrame()
    ov_rows = []
    if len(ov):
        ov = ov[ov["direction"] != "mixed"].copy()
        ov["period"] = ov["created_utc"].map(lambda t: period_of(t, periods))
        w = ov["created_utc"].map(lambda s: es.offhours_window(P, s))
        ov["t0"], ov["t1"] = [x[0] for x in w], [x[1] for x in w]
        ov["ar"] = es.abnormal(P, pd.DatetimeIndex(ov["t0"]), pd.DatetimeIndex(ov["t1"]), b)
        # Calibration finding (Twelve-Day War): almost every war night has an Iran post, so a
        # placebo of post-free war nights is empty. Off-hours events are therefore tested by
        # comparing escalating with de-escalating nights (permutation of the labels); nights
        # in the pre-war estimation window are reported only to show the scale of the noise.
        night = []
        for d0, d1 in zip(est_days[:-1], est_days[1:]):
            night.append(es.abnormal(P, pd.DatetimeIndex([P.last[d0]]),
                                     pd.DatetimeIndex([P.first[d1] + pd.Timedelta(minutes=29)]), b)[0])
        night = np.array(night)
        ov["ar_beyond"] = False
        for d in ("escalating", "de-escalating"):
            g = ov[ov["direction"] == d]
            if len(g):
                ov_rows.append({"sample": "off hours, next open", "test": f"{d} mean AR", "n": len(g),
                                "mean_bps": g["ar"].mean() * BPS, "median_bps": g["ar"].median() * BPS,
                                "p_one_sided": np.nan, "share_beyond_placebo_95": np.nan})
        esc = (ov["direction"] == "escalating").values
        if esc.any() and (~esc).any():
            vals = ov["ar"].values
            obs = vals[esc].mean() - vals[~esc].mean()
            perm = np.array([(lambda m: vals[m].mean() - vals[~m].mean())(rng.permutation(esc))
                             for _ in range(config.PLACEBO_DRAWS)])
            ov_rows.append({"sample": "off hours, next open", "test": "escalating minus de-escalating",
                            "n": len(ov), "mean_bps": obs * BPS, "median_bps": np.nan,
                            "p_one_sided": float((perm >= obs).mean()), "share_beyond_placebo_95": np.nan})
        ov_rows.append({"sample": "off hours, next open", "test": "pre-war nights (scale only)",
                        "n": len(night), "mean_bps": night.mean() * BPS, "median_bps": np.median(night) * BPS,
                        "p_one_sided": np.nan, "share_beyond_placebo_95": np.nan,
                        "sd_bps": night.std() * BPS})
    summary = pd.DataFrame(rows + ov_rows)

    # ---------------- H2 (test set only)
    h2 = None
    if ep.phase_split and reg["period"].nunique() == 2:
        sign = np.where(reg["direction"] == "escalating", 1.0, -1.0)
        s = sign * reg["ar"].values
        ph = (reg["period"] == "phase1").values
        sd = {k: v.std() for k, v in pools.items()}
        z = s / reg["period"].map(sd).values         # size relative to each phase's noise
        rows_h2 = []
        for name, vals in (("signed AR, bps", s * BPS), ("|AR|, bps", np.abs(reg["ar"].values) * BPS),
                           ("signed AR / placebo SD", z)):
            obs = vals[ph].mean() - vals[~ph].mean()
            perm = np.array([(lambda m: vals[m].mean() - vals[~m].mean())(rng.permutation(ph))
                             for _ in range(config.PLACEBO_DRAWS)])
            rows_h2.append({"measure": name, "phase1_mean": vals[ph].mean(), "phase2_mean": vals[~ph].mean(),
                            "n1": int(ph.sum()), "n2": int((~ph).sum()), "difference": obs,
                            "p_one_sided_phase1_larger": float((perm >= obs).mean())})
        h2 = pd.DataFrame(rows_h2)

    meta = {"episode": ep_name, "beta": b, "beta_se": beta["se"], "beta_k": k_beta, "beta_n": beta["n"],
            "estimation_days": len(est_days), "directional_posts": len(posts),
            "regular_events": len(reg), "mixed_dropped": n_mixed,
            "offhours_events": len(ov) if len(ov) else 0}
    if write:
        config.RESULTS_DIR.mkdir(exist_ok=True)
        keep = ["created_utc", "period", "direction", "new", "n_posts", "post_ids", "t0", "t1",
                "r_ita", "r_spy", "ar", "pct_in_placebo", "ar_beyond", "pre_ar", "pre_ar_beyond"]
        out = reg[keep].copy()
        for c in ("r_ita", "r_spy", "ar", "pre_ar"):
            out[c + "_bps"] = out.pop(c) * BPS
        out["ny_time"] = out["created_utc"].dt.tz_convert(NEW_YORK).dt.strftime("%Y-%m-%d %H:%M")
        out.to_csv(config.RESULTS_DIR / f"{ep_name}_events.csv", index=False)
        if len(ov):
            o = ov[["created_utc", "period", "direction", "new", "n_posts", "post_ids", "t0", "t1", "ar", "ar_beyond"]].copy()
            o["ar_bps"] = o.pop("ar") * BPS
            o.to_csv(config.RESULTS_DIR / f"{ep_name}_offhours_events.csv", index=False)
        summary.to_csv(config.RESULTS_DIR / f"{ep_name}_summary.csv", index=False)
        placebo.to_csv(config.RESULTS_DIR / f"{ep_name}_placebo.csv", index=False)
        pd.DataFrame([meta]).to_csv(config.RESULTS_DIR / f"{ep_name}_meta.csv", index=False)
        if h2 is not None:
            h2.to_csv(config.RESULTS_DIR / f"{ep_name}_h2.csv", index=False)
    return {"meta": meta, "events": reg, "summary": summary, "placebo": placebo, "h2": h2,
            "offhours": ov, "pools": pools}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode", required=True, choices=sorted(config.EPISODES))
    ap.add_argument("--beta-k", type=int, default=config.BETA_RETURN_MIN)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    res = run(a.episode, a.beta_k, write=not a.no_write)
    pd.set_option("display.width", 200)
    print(res["meta"])
    print("\nPlacebo:\n" + res["placebo"].round(2).to_string(index=False))
    print("\nTests:\n" + res["summary"].round(3).to_string(index=False))
    if res["h2"] is not None:
        print("\nH2:\n" + res["h2"].round(3).to_string(index=False))
    ev = res["events"]
    cols = ["created_utc", "period", "direction", "new", "n_posts", "ar", "pct_in_placebo", "pre_ar"]
    show = ev[cols].copy()
    show["ar"] *= BPS; show["pre_ar"] *= BPS
    show["created_utc"] = show["created_utc"].dt.tz_convert(NEW_YORK).dt.strftime("%m-%d %H:%M")
    print("\nRegular-hours events (AR in bps):\n" + show.round(1).to_string(index=False))


if __name__ == "__main__":
    main()
