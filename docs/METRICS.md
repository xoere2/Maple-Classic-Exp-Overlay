# Metrics: what each number is, and how much to trust it

Every number in the fork carries a **kind**. You see it as a symbol in the overlay, and
written out in Analytics, the CLI and exports:

| Kind | Symbol | Meaning |
|---|---|---|
| observed | (none) | Read from the screen and confirmed (EXP, wallet, HP/MP, party, map) |
| manual | ✎ | Typed in by you (notes, snapshots, prices, tags, monster info) |
| calculated | (none) | Arithmetic on observed/manual values only |
| inferred | ≈ | Follows from an accounting identity; exact only if your notes are complete |
| estimated | ~ | A model or heuristic. Treat with care |
| unavailable | — | Can't be worked out from what was recorded (the reason is shown) |

Rates over time also give a confidence (`high`/`medium`/`low`). Experimental counts give
a 95% interval.

## Time

| Metric | Kind | How |
|---|---|---|
| Real time | observed | Start → Stop, minus Pause and AFK |
| Active time | calculated | Upstream's rule, recomputed from the EXP events. The clock stops `gap` s (default 15) after the last gain and restarts at the next gain or at Start/Resume. With auto-pause off, active = real |
| Idle time | calculated | Real − active: waiting for respawns, congestion, looting, walking |
| AFK / paused | observed | Marked by you (AFK button / Shift+5, Pause) |

## EXP

| Metric | Kind | How |
|---|---|---|
| EXP gained | observed | Sum of trusted gains (upstream's checks: EXP table, confirmations). Gains while upstream is auto-paused **are** counted. Death losses aren't subtracted |
| EXP lost | observed | Deaths × loss, shown separately |
| Real EXP/hr | calculated | EXP ÷ real time. **The default for comparisons** |
| Active EXP/hr | calculated | EXP ÷ active time (close to upstream's number) |
| Last 1/5/15 min | calculated | EXP in the last N minutes of real time. Early on it's over what there is, labelled "so far" |
| Time to level | calculated | (level's EXP − current) ÷ 5-min rate (or real rate) |
| Per map, solo vs party | calculated | The same, split at map and party-size changes |

## Mesos (no double counting)

The wallet already includes every purchase and sale, so spending is **never subtracted
again**. Notes only explain the wallet:

| Metric | Kind | Formula |
|---|---|---|
| Wallet change | observed | last − first confirmed mesos reading (keep the inventory open) |
| Transfers | manual | storage deposits/withdrawals and mesos to/from other characters |
| **Realized net** | observed/manual | wallet change − transfers. Cash view: drops when you stock up on potions |
| Spending / sales | manual | from your notes |
| **Pickups** | inferred ≈ | realized net + spending − sales |
| Consumables used | calculated (or ~) | used × your price. "Used" comes from start/end snapshots (observed), "used" notes (manual), or HP/MP potion estimates (estimated) |
| Stock change | calculated | consumables bought − used, at your price: potions still in the bag aren't a loss |
| Loot value | estimated ~ | (looted − sold) × your price: unsold, so an estimate |
| **Operating profit** | inferred | pickups + loot sold − consumables used − recharges/expenses. "Is this map profitable?" It doesn't depend on when potions were bought |
| **Estimated total** | estimated ~ | realized net + stock change + unsold loot (+ equipment bought, which counts as an investment, not a farming cost) |
| Unexplained jumps | observed | big wallet changes with no note within 2 min. They lower the confidence of realized/pickups |

Worked example (also a test): start 100,000; buy 100 Blue Potions (22,000) mid-session;
end 120,000; 30 potions used. Realized net = 20,000. Pickups = 42,000. Consumables used
= 6,600. Operating profit = 35,400. Estimated total = 20,000 + 70 × 220 = 35,400.

## HP / MP

| Metric | Kind | How |
|---|---|---|
| MP spent, HP lost | observed | sums of drops between readings (~1.5 s apart). Level-ups and max changes are ignored |
| Skill casts | estimated ~ | MP drops that are whole multiples of the skill's cost (Lucky Seven: 16, as given). Casts hidden inside a potion's rise in the same reading are recovered |
| Potions drunk | estimated ~ | rises above half a potion's restore value, or rises that fill exactly to max (more than 25). In a party, HP rises may be a Cleric's Heal: low confidence |
| MP per kill, EXP per MP, mesos per MP | calculated from the above | the weaker kind of their parts |

## Monsters (experimental)

| Metric | Kind | How |
|---|---|---|
| Observed deaths/hr | estimated ~ | monsters that vanished in the middle of the region (not at an edge) |
| Personal kills/hr | estimated ~ | **confirmed** (solo + damage numbers on it + EXP within 2.5 s) + **probable** (solo + EXP, or party + damage + EXP) |
| Party credit | estimated ~ | in a party, EXP came but anyone may have killed it. **Never counted as yours** |
| Unattributed | estimated ~ | vanished with no evidence: someone else's kill, a despawn, or a detection miss |
| Estimated kills from EXP | estimated ~ | solo only: EXP ÷ the monster's EXP (that you entered). Never in a party |
| Observed respawns/hr | estimated ~ | monsters appearing mid-region (not walking/scrolling in, not already there). **Selected region only, never map-wide** |
| Wave interval, deaths per wave | estimated ~ | spawns within 4 s form a wave |
| Visible monsters, empty share | estimated ~ | from sampled frames |
| Your share / competition | estimated ~ | probable+confirmed ÷ deaths; unattributed ÷ deaths (solo only) |
| Attacks per kill, one-shot % | estimated ~ | new damage numbers on the monster in its last 4 s |
| Drop rate | observed + estimated | drops (snapshots/notes) ÷ personal kills, with an exact Clopper-Pearson 95% interval |

## Rankings

`Best EXP/hour` · `Best realized mesos/hour` · `Best estimated total profit/hour` ·
`Best EXP per meso spent` · `Best EXP/hour without losing mesos` (profit basis
selectable: operating (default), realized, estimated). Sessions too short, legacy log
lines (active time only), or ones missing the needed numbers are listed separately, with
the reason.
