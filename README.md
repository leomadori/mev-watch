# MEV Watch

**Live:** https://leomadori.github.io/mev-watch/

A research page that shows how busy Ethereum is and how much **sandwich bots** take from ordinary swaps, and who makes the money. It only reads public data and never sends transactions.

## Gas & congestion

- **Live tiles** (refresh every 12 seconds, straight from a public Ethereum node): base fee, typical priority tip, how full the latest block is, and what a simple ETH transfer and a Uniswap swap cost right now in dollars.
- **Hourly charts** for the last 48 hours: average base fee and block fullness.

## Sandwich tracker

A *sandwich* is when a bot sees your pending swap, buys just before you (pushing the price up), lets your swap go through at the worse price, and sells right after you. The page explains this step by step with the biggest real example from the last 24 hours, linked to Etherscan.

- **Totals for the last 24 hours:** sandwiches, victim trades, active bots, the bots' gross gain, how much of it went to gas, and what was left.
- **Who profits:** a leaderboard of bot contracts (24 hours / 7 days / all time) with gross gain, gas paid, the share they kept, and when each bot was first and last seen. Every bot ever detected can be downloaded as [`data/bots.csv`](data/bots.csv).
- **Latest sandwiches:** every detected sandwich with links to the front-run, victim and back-run transactions.
- **Daily charts** once there's more than one day of data.

One early finding: most bots hand almost all of their gain to the block builder as gas, often 90% or more, in order to win their spot in the block.

## How it works

[`scan.py`](scan.py) (Python, standard library only) runs every 2 hours. It reads every confirmed block since the previous run from a free public node ([publicnode.com](https://ethereum-rpc.publicnode.com)) and looks at the swap events of Uniswap V2/V3-style pools. A sandwich is counted when:

1. the same bot (same sender, or the same private contract) trades a pool,
2. at least one other trader then swaps the same pool in the same direction,
3. the bot then sells back roughly what it bought (within 10%).

Contracts used by several different wallets in the same block (routers and aggregators) don't count as a bot. Transactions that also add or remove liquidity in the pool ("just-in-time liquidity", a different strategy) are skipped. If the bot traded other pools in the same transaction, the sandwich is counted but its gain is shown as "complex", because this pool alone doesn't show what it made. Gains in ETH, USDC, USDT or DAI are converted to dollars with the ETH price from CoinGecko.

## Limits

- Covers Uniswap V2/V3-style pools (Uniswap, SushiSwap and forks). Uniswap V4, Curve, Balancer and sandwiches across several pools are not detected.
- "Gas" includes the priority tip paid to the builder. Some bots also pay builders with a direct transfer, which isn't visible in receipts, so real profits are **lower** than shown.
- Failed or outbid attempts never reach the chain, so the full cost of competing isn't visible.
- This repository contains the page, the scanner, the latest summary (`data/mev.js`) and the all-time bot table (`data/bots.csv`). The day-by-day history is kept forever but stays local.
- Scans run on a Mac every 2 hours; if it sleeps for more than about 3 hours, the blocks from that stretch are never scanned, so totals are a lower bound on activity.

## Changelog

- **2026-10-29:** "All time" tab in Who Profits with first/last seen dates, a CSV download of every bot, and the scan history is no longer deleted after 30 days.
- **2026-10-12:** First version: live gas tiles, hourly gas charts, sandwich detection, bot leaderboard, latest sandwiches.

---

*Read-only research page. Not financial advice.*
