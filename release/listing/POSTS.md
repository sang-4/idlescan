# Announcement drafts

Factual tone. No outcome claims. Every draft ends with the disclaimer line,
because a post can travel without the product page.

## 1. Long form (X/Twitter thread)

**1/**
I built idlescan Desk: a local airdrop research tool for EVM wallets.

It reads balances across 14 chains, checks merkle claims against the
distributor's on-chain root, and ranks positions by net after gas.

Runs on your machine. Python + SQLite. No account, no cloud.

**2/**
Most "airdrop checkers" only ask whether an index was claimed. That is true for
almost every address.

idlescan asks two separate questions: was this address owed anything, and has
that allocation already been taken? If it cannot verify the first, it says so.

**3/**
Net value is the point. A claim worth $40 that costs $25 in fees is not a $40
claim. Positions are ranked after an EIP-1559-aware fee quote, with a buffer, and
each gas figure is labelled full, partial or unavailable.

**4/**
Every proof is re-derived and checked against merkleRoot() before it is shown. A
bad dataset or a tampered proof shows up as an error, not a number you might act
on.

**5/**
It never holds keys, signs, or broadcasts. Read-only is the default. If you turn
on the optional hand-off, the server returns an unsigned transaction for your own
wallet to review and sign. No token approvals are ever generated.

**6/**
Other things in the box: immutable snapshots with diffs, alerts for deadlines and
below-gas positions, JSON/CSV/Markdown exports, an iCalendar feed, a documented
JSON API with optional bearer auth, and a doctor command for support.

**7/**
It is self-hosted and dependency-free at runtime. Demo mode and an offline
selftest let you see every screen before adding a wallet. Documentation includes
the full list of hosts it can contact and commands to audit the package.

**8/**
Founding Release, v1.2.0. $39 on Gumroad (list $49). 14-day refund window.
Docs: https://allank.dev

Research tool, not financial advice. Verify contract addresses against official
sources before signing anything.

## 2. Short post

Built idlescan Desk: a local, self-hosted airdrop research tool for EVM wallets.
14 chains, merkle proofs checked against the on-chain root, positions ranked by
net after gas. No keys, no signing, no cloud. Founding Release $39, 14-day
refund. Research tool, not financial advice.

## 3. Community post

**A local desk for checking airdrop eligibility and claim cost**

I have been building idlescan Desk, a self-hosted tool for EVM wallet research.
It is aimed at people who want to check eligibility without pasting addresses
into a website and without trusting a dashboard's number.

What it does:

- Reads native and curated token balances across 14 EVM chains.
- Separates "was I owed anything" (dataset, claim API or allocation view) from
  "was it already claimed" (live distributor read).
- Re-derives every merkle proof and checks it against the on-chain root.
- Ranks claims by net after gas, with an explicit quality label on each estimate.
- Keeps immutable snapshots with diffs, alerts, exports and a calendar feed.

What it does not do: hold keys, sign, broadcast, or request approvals. Read-only
is the default; the optional hand-off returns an unsigned transaction for your
own wallet.

It is a Python package with zero runtime dependencies and a local SQLite
database. Demo mode and an offline self-test are included. The documentation
lists every host it can contact and how to audit the package yourself.

Founding Release v1.2.0 is $39 on Gumroad, with a 14-day refund window. Happy to
answer technical questions here.

Research tool, not financial advice. Verify contract addresses against official
sources before signing.
