"""Robustness checks on the 2026 test set -> results/iran_war_2026_robustness.csv

Usage:
    python -m src.robustness

1. Beta from 30-minute returns instead of 5-minute.
2. Placebo matched on hour of day (intraday volatility is U-shaped).
3. How big could the H1 effect be? One-sided 95% upper bounds on the signed mean,
   from (a) the placebo noise, (b) the events' own spread (t, 17 df), (c) a bootstrap.
   Plus the effect size the test could detect 80% of the time (power).
4. Exploratory (not specified before the test run): H2 on the off-hours events, i.e.
   the signed overnight abnormal return in phase 1 vs phase 2, permutation test.
"""

import numpy as np
import pandas as pd

from src import config
from src import event_study as es
from src.market_hours import NEW_YORK
from src.run_study import periods_for, run

BPS = 1e4
T95_17DF = 1.7396          # one-sided 95% point of Student's t with 17 degrees of freedom
Z95, Z80 = 1.6449, 0.8416  # normal quantiles: one-sided 5% test, 80% power


def row(check, sample, statistic, n, value_bps, p=np.nan):
    return {"check": check, "sample": sample, "statistic": statistic, "n": n,
            "value_bps": value_bps, "p_one_sided": p}


def main() -> None:
    rows = []
    rng = np.random.default_rng(config.SEED)
    ep = config.EPISODES["iran_war_2026"]

    # 1. 30-minute beta
    r30 = run("iran_war_2026", k_beta=config.BETA_ROBUST_MIN, write=False)
    s = r30["summary"]
    for _, x in s[s["test"].isin(["signed mean (H1)", "escalating minus de-escalating"])].iterrows():
        rows.append(row(f"beta from {config.BETA_ROBUST_MIN}-min returns (beta {r30['meta']['beta']:.3f})",
                        x["sample"], x["test"], x["n"], x["mean_bps"], x["p_one_sided"]))

    # main run, reused below
    r = run("iran_war_2026", write=False)
    ev, pools, beta = r["events"], r["pools"], r["meta"]["beta"]
    P = es.load_prices()
    sign = np.where(ev["direction"] == "escalating", 1.0, -1.0)
    signed = sign * ev["ar"].values
    n = len(signed)

    # 2. hour-of-day matched placebo
    times = pd.read_csv(config.POST_TIMES_CSV, parse_dates=["created_utc"])["created_utc"]
    post_ns = np.sort(times.dt.as_unit("ns").astype("int64").values)
    sims = np.zeros(config.PLACEBO_DRAWS)
    for name, a, z in periods_for(ep):
        days = [d for d in P.days if a <= d <= z]
        starts = es.placebo_starts(P, days, post_ns, pd.Timedelta(minutes=config.PLACEBO_CLEAR_MINUTES))
        ar = es.abnormal(P, starts, starts + es.WINDOW, beta)
        hour = starts.tz_convert(NEW_YORK).hour
        for _, e in ev[ev["period"] == name].iterrows():
            pool = ar[hour == e["t0"].tz_convert(NEW_YORK).hour]
            sims += (1.0 if e["direction"] == "escalating" else -1.0) * rng.choice(pool, config.PLACEBO_DRAWS)
    sims /= n
    rows.append(row("placebo matched on hour of day", "regular hours, all", "signed mean (H1)", n,
                    signed.mean() * BPS, float((sims >= signed.mean()).mean())))

    # 3. upper bounds and power
    se_placebo = sims.std()
    se_events = signed.std(ddof=1) / np.sqrt(n)
    boot = np.array([rng.choice(signed, n).mean() for _ in range(20_000)])
    rows += [
        row("upper bound on H1 effect", "placebo noise (SE %.1f bp)" % (se_placebo * BPS),
            "one-sided 95% bound", n, (signed.mean() + Z95 * se_placebo) * BPS),
        row("upper bound on H1 effect", "events' own spread, t(17) (SE %.1f bp)" % (se_events * BPS),
            "one-sided 95% bound", n, (signed.mean() + T95_17DF * se_events) * BPS),
        row("upper bound on H1 effect", "bootstrap of the 18 events", "95th percentile of mean", n,
            np.quantile(boot, 0.95) * BPS),
        row("power", "placebo noise", "effect detected 80% of the time", n, (Z95 + Z80) * se_placebo * BPS),
        row("power", "events' own spread", "effect detected 80% of the time", n, (Z95 + Z80) * se_events * BPS),
        row("if the true effect were +10 bp", "events' own spread", "chance of a result this low", n,
            np.nan, float(np.mean(rng.normal(10e-4, se_events, 100_000) <= signed.mean()))),
    ]

    # 4. exploratory overnight H2
    o = pd.read_csv(config.RESULTS_DIR / "iran_war_2026_offhours_events.csv")
    so = np.where(o["direction"] == "escalating", 1, -1) * o["ar_bps"].values
    ph = (o["period"] == "phase1").values
    obs = so[ph].mean() - so[~ph].mean()
    perm = np.array([(lambda m: so[m].mean() - so[~m].mean())(rng.permutation(ph))
                     for _ in range(config.PLACEBO_DRAWS)])
    for name, m in (("phase1", ph), ("phase2", ~ph)):
        rows.append(row("exploratory: H2 on off-hours events", name, "signed overnight AR",
                        int(m.sum()), so[m].mean()))
    rows.append(row("exploratory: H2 on off-hours events", "phase1 minus phase2", "difference",
                    len(o), obs, float((perm >= obs).mean())))

    out = pd.DataFrame(rows)
    out.to_csv(config.RESULTS_DIR / "iran_war_2026_robustness.csv", index=False)
    print(out.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
