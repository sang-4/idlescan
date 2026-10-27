# Screenshot and recording shotlist

Capture from demo mode so no real wallet data appears. Demo mode seeds a synthetic
portfolio and a synthetic airdrop called `demo-merkle`.

## Start demo mode

```bash
idlescan serve --demo
```

Open <http://127.0.0.1:8787>. Recommended browser width: **1440 px** for desktop
shots, **390 px** for the one phone shot. Use a clean browser profile at 100 %
zoom, hide bookmarks, and close other tabs. Wait for the Overview data to render
(the demo snapshot is created at startup).

## Eight screenshots

| # | Route | Highlight | Suggested caption |
| --- | --- | --- | --- |
| 1 | `#/overview` | The combined-value stat row and the portfolio pulse, before any scrolling | "One screen: recoverable value, idle balances, deadlines" |
| 2 | `#/claims` | The gas, net and proof columns, and the gas-estimate footnote at the bottom of the panel | "Every claim ranked by net after gas, with proof status" |
| 3 | `#/drops` | The safety badge and dataset-status column on the demo airdrop card | "Provenance and dataset status before you act" |
| 4 | `#/drop/demo-merkle` | Eligibility source, proof counts and the contract links panel | "Airdrop profile: contracts, proofs, history, exports" |
| 5 | `#/check` | An empty checker with the address field focused | "Check any address without adding it to your watchlist" |
| 6 | `#/wallets` | The three demo wallets and their rollups | "Watch-only wallets with per-wallet totals" |
| 7 | `#/activity` | Snapshots list and the diff button | "Immutable snapshots and side-by-side diffs" |
| 8 | `#/settings` | The read-only badge, bind line and security-model panel | "Read-only by default; the UI cannot flip the mode" |
| 9 (optional, 390 px) | `#/overview` | The same page at phone width, showing reflowed stats | "Responsive from 320 px to 1440 px" |

Naming: `01-overview-1440.png` … `08-settings-1440.png`, plus
`09-overview-390.png`. PNG, no annotations over the UI; add captions in the
listing, not on the image.

## 45-second screen recording

One continuous take, no narration required (add captions later).

| Time | Action |
| --- | --- |
| 0:00–0:05 | Start on `#/overview`. Move the pointer slowly across the stat row and sparkline. |
| 0:05–0:12 | Click **Claims**. Scroll two rows, then hover the net column. Pause on the gas footnote. |
| 0:12–0:20 | Click a drop name to open `#/drop/demo-merkle`. Scroll to the proof and provenance panels. |
| 0:20–0:28 | Open the **Activity** page and the latest snapshot diff. Show the added/changed rows. |
| 0:28–0:36 | Go to `#/check`, type an address, run the check, show the result table. |
| 0:36–0:45 | Open `#/settings`, show the read-only badge and the security-model list. End on the version badge. |

Record the browser window only. Do not show the terminal, your desktop, or any
real address. The demo wallets are obviously synthetic (`Demo cold wallet`, etc.).

## If a shot will not render

Reload the page. Demo data is created only when the database has no wallets; if
you have used the same database for real data, start demo mode with a throwaway
database:

```bash
idlescan serve --demo --db /tmp/idlescan-demo.db
```

Delete that file afterwards.
