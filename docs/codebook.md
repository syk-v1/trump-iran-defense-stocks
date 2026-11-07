# Codebook: tagging Trump's posts about the Iran conflict

Every candidate post (`src/candidates.py`) gets three fields. Posts are labelled in time
order, because "repeat" depends on what came before.

## 1. `about_iran`: yes / no

**yes** if the post is about the US/Israel conflict with Iran: Iran's government,
leaders, military or nuclear programme; strikes on Iran or by Iran (and by its proxies,
as part of this war); shipping in the Strait of Hormuz; ceasefires, talks or deals with
Iran; or oil prices explicitly tied to the war.

**no** for everything else, even if it matched a keyword: Gaza and hostages, Ukraine and
Russia, the "One Big Beautiful Bill", domestic "war" talk, an "oil" post about drilling,
or Israel with no Iran angle.

## 2. `direction` (only when about_iran = yes)

The question is: **does the post make a bigger, longer or wider US/Israel–Iran conflict
more likely, or less likely, than before it?** It's about what the post signals for
the future, not whether its tone is angry or happy.

| Label | Signals | Examples of the signal |
|---|---|---|
| `escalating` | more conflict | announces or threatens strikes; ultimatums and deadlines; "unconditional surrender"; rejects talks; says a ceasefire is broken or over; new deployments; threats to Iran's leaders; regime change; closing or attacking Hormuz shipping |
| `de-escalating` | less conflict | announces or confirms a ceasefire; talks, progress or a deal; "peace"; extends a deadline; pauses strikes; says the war is over; thanks mediators for peace; asks Israel to hold back |
| `neutral` | no change | about Iran but backward-looking or political: praise for a strike that already happened, attacks on media coverage of strike damage, domestic politics of the war, oil-price exhortations, reposts of praise; or signals in both directions with neither dominant |

Rules for hard cases:
- **Both directions in one post:** label by the forward-looking signal that dominates
  (e.g. "we will hit them very hard unless they make a deal" is `escalating`, since the
  threat is the operative signal). If they are truly balanced: `neutral`.
- **Claims about the past:** "we destroyed their nuclear sites" is `neutral`, unless it
  also signals what comes next.
- **Quotes and reposts of others:** label what the post signals by Trump choosing to
  share it.

## 3. `new_info`: new / repeat (only when about_iran = yes)

**new** if the post carries forward-looking information Trump had not already posted in
this episode (a new threat, deadline, ceasefire, talks, strike). **repeat** if it restates,
re-shares or copies something he already posted: reposts of his own posts, link-only
posts pointing to his earlier post, the same message re-posted, or a recap. Neutral posts
are marked too, but the analysis uses `new_info` mainly for escalating and de-escalating posts.

## Who labelled

All labels in `labels/labels.csv` were written by Claude (an LLM), reading each post in
full against this codebook. No human labels exist yet. `labels/human_check_sheet.csv` is
a stratified sample of 200 candidates with blank columns for a human coder;
`python -m src.agreement --human` compares those against Claude's labels.
