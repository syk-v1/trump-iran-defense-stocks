# Decisions and data notes

Written down as they are made, so the test-set run can't be quietly tuned afterwards.

## Step 1: posts (2026-11-07)

**Source.** trumpstruth.org RSS feed: `/feed?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&limit=N`,
`limit` at most 500.

**What probing the feed showed**
- Day boundaries are **UTC**, and `end_date` is inclusive. Querying 21 June 2025 returns
  02:49 to 23:50 UTC, and its last post (23:50:59 UTC = 19:50 EDT) is the US-strikes announcement.
- March 2026 alone has more than 500 posts, so a month-long request gets silently cut off.
  **Decision:** one request per UTC day; a day returning 500 posts is an error, not a warning.
- Truth Social IDs are Mastodon-style snowflakes: `id >> 16` is the creation time in ms.
  It matches `pubDate` to the second, so it gives millisecond timing and an independent check.
- Post types: `original`, `quote`, `repost`. Removed posts stay in the feed with
  `removed = true` and the time removal was noticed.

**Decisions**
- **Collect all posts, not only Iran ones**, from 30 days before to 30 days after each
  episode (`PAD_DAYS`). The placebo step needs to know every post time to pick
  post-free moments, and the beta window sits before the episode.
- **Reposts:** the words are stored once, in `quoted_text`; `text` is empty. Quote
  posts keep Trump's own words in `text` and the quoted words in `quoted_text`.
- **Removed posts are kept**, with a flag. Whether to exclude them is a later decision.
- **Raw downloads are kept unchanged** in `data/raw/posts/`. Days fetched before they
  were a full day old are downloaded again automatically; `--refresh` re-downloads all.

**First full run (2026-11-07)**: 8,457 posts across 354 UTC days.
- Twelve-Day War window (14 May to 24 Jul 2025): 1,268 posts; 210 inside 13 to 24 June,
  of which 39 mention "Iran" in their text.
- 2026 window (29 Jan to 6 Nov 2026): 7,189 posts; 411 of 6,475 from 28 Feb mention "Iran".
- `pubDate` and the ID timestamp agree to within 1 s for every post.
- About 31% of posts (2,604) are media-only: an image or video with no text.
- 2026-06-07 has zero posts, while its neighbours have 13 to 30.

**Decisions after review (2026-11-07)**
- **Media-only posts are dropped** from `posts.csv`, the study's sample. Their times
  are still saved in `post_times.csv` (`has_text = False`), so the placebo step can avoid
  the minutes after *every* post, not only the text ones.
- **2026-06-07 is treated as a quiet day** and used as normal.
- **The 2026 episode ends on 16 June 2026**, the day before Trump and Pezeshkian signed
  the Pakistan-brokered Islamabad Memorandum of Understanding (17 June), a 14-point
  framework for ending hostilities. Why that date:
  - It is the best-documented change of regime after the 8 April ceasefire.
  - Sources disagree about what came after it: Iran disputes the 60-day deadline, one
    account says the ceasefire collapsed on 8 July over attacks on shipping, and
    others report talks in August about extending it. Including that period would mix
    renewed fighting into H2's "ceasefire" phase.
  - It gives H2 two phases: 28 Feb to 7 Apr (39 days of war) and 8 Apr to 16 Jun
    (70 days of ceasefire).
  - Posts after 16 June are out of scope. They could be used later as a robustness
    check, but not as part of the test.
  - Sources: [Wikipedia: 2026 Iran war ceasefire](https://en.wikipedia.org/wiki/2026_Iran_war_ceasefire),
    [Wikipedia: Islamabad Memorandum](https://en.wikipedia.org/wiki/Islamabad_Memorandum),
    [House of Commons Library CBP-10637](https://commonslibrary.parliament.uk/research-briefings/cbp-10637/),
    [Express Tribune, signing announcement](https://tribune.com.pk/story/2613815/pm-shehbaz-announces-signing-of-islamabad-mou-by-us-iranian-leadership).

**Open**
- [ ] Off-hours posts: drop vs next open (step 4). `market_hours.market_session` labels them.

## Step 2: prices (2026-11-07)

**Source.** Alpaca market data, free plan: `GET /v2/stocks/bars`, 1-minute bars, both
tickers in one request, at most 200 requests per minute, no access to the latest 15 minutes.

**Decisions**
- **SIP feed** (every US exchange), not IEX (one exchange, a few % of volume). With IEX,
  ITA would have many minutes with no trades. To confirm on the first download: Alpaca's
  docs don't say whether the free plan includes SIP history.
- **Prices adjusted for splits and dividends** (`adjustment=all`). Event windows within one
  day don't change, but returns across an ex-dividend night would otherwise show a fake
  drop of about 0.2–0.4%, the same size as the effects we're looking for. All days are
  downloaded in one run, so they share the same adjustment.
- **04:00–20:00 New York time is kept** (pre-market and after-hours too), so the
  drop-vs-next-open choice in step 4 is still open.
- **Minutes with no trades are not filled in here.** Alpaca sends no bar for them. How
  to treat them (carry the last price forward, or skip) belongs in step 4. The summary
  reports how many regular-session minutes each ticker is missing.
- **Bar label:** a bar's timestamp is its start (09:30 = 09:30:00–09:30:59). Confirmed
  below. Because of this, the closing-auction bar (16:00) counts as the "post" session.

**First download (2026-11-07)**: 209,341 bars, 173 weekdays.
- The free plan allows SIP history.
- Every expected trading day has bars, and no NYSE holiday does, so the holiday list
  in `config.py` matches the data.
- **Bar labels confirmed:** SPY's median volume goes from 26k shares (09:29 bar) to
  644k (09:30 bar). The jump is in the 09:30 bar, so bars are labelled by their start.
- The closing auction is in the 16:00 bar: median 1.6M shares in only about 1,200 trades
  (a few big blocks). On many days the 15:59 minute has more volume (about 12,900 trades).
  There are small late-reported volume bumps around 16:11.
- **SPY has every regular-session minute. ITA does not:** it is missing 2,048 of 18,930
  minutes in the 2025 window (89% covered) and 2,780 of 45,240 in 2026 (94% covered).
  The gaps are short: the longest gap in a day is 2 minutes on a median day and 6 at
  most. ITA has a 09:30 bar every day, and a median of 34 trades a minute.
- ITA trades thinly outside regular hours (on 16 June 2025, 359 bars between 04:00 and
  20:00, against 898 for SPY). That makes off-hours prices noisy, which counts in favour
  of measuring from the next open (step 4).

**Open (step 4)**
- [ ] ITA minutes with no trades: carry the last price forward (natural with gaps this
      short), or measure only between minutes that both have trades.

## Step 3: tagging (2026-11-07)

- Candidates: wide keyword net (`src/candidates.py`) on each episode's core dates. It was
  calibrated by reading **all 150** Twelve-Day War text posts. The first list missed 5 Iran
  posts (the Qatar base attack, "IT'S TIME FOR PEACE", "made PEACE abroad", "KEEP OIL
  PRICES DOWN… ENEMY", a quote about war). Adding *qatar, peace, war, oil, bomb, missile,
  drone* caught all 5. Link-only posts pointing to Trump's own earlier posts are resolved
  to that post's text (15 of 107 resolvable).
- 427 candidates, all labelled by Claude against `docs/codebook.md` (written before
  labelling), in time order. About Iran: 52 of 55 (2025) and 258 of 372 (2026).
- **No human labels.** The second coder is a keyword-rule classifier (`src/agreement.py`):
  about_iran agreement 91% (kappa 0.79); direction agreement 50% (kappa 0.24), mostly
  because the rules call backward-looking boasts "escalating". On the posts Claude called
  escalating or de-escalating, the rules point the opposite way 23 times out of 104.
  `labels/human_check_sheet.csv` has 200 posts for a human coder.
- Lebanon/Hezbollah posts are **not** coded as Iran posts (separate front, separate war).
- Op-ed links are `neutral` whatever their headline.

## Step 6: calibration on the Twelve-Day War, then freeze (2026-11-07, before any 2026 run)

| Setting | Frozen value | What the development set showed |
|---|---|---|
| Beta returns | 5-minute (`BETA_RETURN_MIN`) | 1-min beta 0.63 → 5-min 0.79 → 10-min 0.79 → 30-min 0.86 (se 0.07): thin ITA trading biases short-interval betas down; 5-min removes most of it with a small se |
| Alpha | 0 | 30-minute alpha is a fraction of a bp |
| Cluster | 30 min from the first post | 3 posts in 27 min on 17 June 2025 form one event |
| Placebo | every eligible t0 with no post of any kind ±30 min, same period | 1,646 windows; mean −0.5 bp, SD 17 bp, 5th/95th −26/+27 bp |
| Regular-hours eligibility | post between 09:31 and 15:30 New York time | only 3 of 33 directional 2025 posts qualify |
| Off-hours test | escalating vs de-escalating nights, permutation | a placebo of Iran-free war nights is empty (nearly every night has an Iran post), so the planned placebo cannot work |
| Draws / seed | 10,000 / 42 | |

Development-set results (method check only, too few events to test anything): 3
regular-hours events (+1.6, −9.4, +17.9 bp); the 30 minutes before the 17 June escalation
already show +31.6 bp (reverse-causality warning). Off-hours: escalating nights +35 and
+45 bp; de-escalating nights −27 and −150 bp (ceasefire night).

## Step 7: the 2026 test, run once with the frozen settings (2026-11-07)

- Beta (5-min, 29 Jan–27 Feb 2026): 1.016 (se 0.038).
- 119 directional Iran posts → 18 regular-hours events (3 in phase 1, 15 in phase 2),
  35 off-hours events, 0 dropped as mixed.
- Placebo: phase 1 6,177 windows (SD 25 bp, 5th/95th −38/+41 bp); phase 2 10,939 (SD 25 bp, −43/+37 bp).
- **H1 not supported in regular hours.** Escalating mean AR −8.3 bp (n 6, p 0.79);
  de-escalating +7.4 bp (n 12, p 0.89); signed mean −7.7 bp (p 0.93). 1 of 18 events beyond
  its placebo cutoff in the predicted direction. New-info only: signed −10.0 bp (p 0.95).
- 30 min before posts: signed −5.6 bp (p 0.85), so no sign of reverse causality in 2026.
- Off hours (next open): escalating nights +25.0 bp (n 22), de-escalating −8.9 bp (n 13);
  difference +34.0 bp, permutation p 0.18. The direction matches H1, but it is not significant.
- **H2 cannot be tested meaningfully in regular hours** (phase 1 has 3 events): signed
  AR −3.6 bp vs −8.5 bp, p 0.42.
- Robustness: 30-min beta (1.042) gives the same answers (signed −7.5 bp, p 0.92;
  off hours +35.2 bp, p 0.17). Exploratory, not pre-specified: off-hours signed AR
  +31.2 bp in phase 1 vs +9.9 bp in phase 2 (difference +21.2 bp, p 0.27). The direction
  matches H2, but it is not significant.

## Verdict (2026-11-07)

Rule (from the design's 95th-percentile criterion, fixed before the test run): a
hypothesis is supported only if its test passes p < 0.05 in the predicted direction.

- **H1: not supported.** Signed mean −7.7 bp (p 0.93). The placebo SD of the 18-event
  mean is 5.9 bp, so the one-sided 95% upper bound on the effect in H1's direction is
  +2.0 bp (revised after review, see below: +5.3 bp using the events' own spread; a true
  +10 bp effect would give a result this low about 1% of the time).
- **H2: not supported.** Phase difference +4.9 bp (p 0.42). With no market-hours effect
  in either phase, there is nothing for the ceasefire to have shrunk.
- The overnight result (+34 bp, p 0.18) and the exploratory overnight H2 (p 0.27) fail the
  rule. They are reported as leads for future work.

## Review before publishing (2026-11-07)

A line-by-line check of the maths and methods before the repo went public.

**Checked and correct**
- Abnormal return `r_ITA − β r_SPY` on simple returns; prices taken at minute boundaries
  *before* the post (no look-ahead); beta estimated only on pre-war days.
- Beta: OLS slope with consistent degrees of freedom (`np.cov` and `np.var`, both ddof=1);
  its SE is `sqrt(s²_resid / Σ(x − x̄)²)`.
- Cohen's kappa `(p_o − p_e) / (1 − p_e)`; Mastodon ID timestamp `id >> 16` = ms.
- Placebo exclusion window is inclusive at both ends; one-sided p-values use the right tail
  for escalating and the left tail for de-escalating; per-period pools for per-period events.
- Time zones: every session test goes through `America/New_York` (DST on 8 Mar 2026 is covered by tests).

**Corrected**
- **Upper bound on the H1 effect.** The 18 event windows are noisier than quiet minutes
  (SD 31.7 bp vs placebo 24.8 bp). So the placebo-based bound (+2.2 bp) understates the
  uncertainty. The bound reported now uses the events' own spread: **+5.3 bp** (t, 17 df);
  the bootstrap gives +4.0 bp. "+10 bp would give a result this low" becomes about 1%
  (was 0.2%).
- **Power.** The earlier "about 10 bp detectable" was the 50%-power point. The effect
  detected 80% of the time is 15 bp (placebo noise) to 19 bp (events' own spread).

**Checked, no change needed**
- Placebo matched on hour of day: p 0.92 (main test 0.93), so time of day doesn't matter here.
- Event days are no noisier than other days at quiet minutes (SD 23.8 vs 24.8 bp).
- 1 of 18 events beyond its cutoff: by chance, at least one would be expected 60% of the time.

**Known, minor, documented rather than changed**
- One event's pre-window overlaps the previous event's window (events 43 min apart).
- Placebo p-values assume event windows are as noisy as quiet ones. Since they are
  noisier, those p-values would *overstate* significance. For a null result this is
  conservative, and the event-based bound above does not rely on it.
- Overnight windows use the intraday beta; weekend windows are longer and noisier than weeknights.
- Labels were written by an LLM with general knowledge of 2026 events, but without
  access to the returns; they were finished before any 2026 return was computed.
