#!/usr/bin/env python3
"""MEV Watch: gas/congestion stats and after-the-fact sandwich detection on Ethereum.

Reads confirmed blocks from a free public node (read-only JSON-RPC, no key) and looks for the
classic sandwich pattern on Uniswap V2/V3-style pools:

    bot buys  ->  victim(s) buy the same way  ->  bot sells back what it bought

Every block since the previous run is scanned (capped at MAX_BLOCKS), so with a run every two hours
nothing is skipped. Profit is estimated from the swap amounts minus the gas the bot paid. Direct
payments to block builders are not visible in receipts, so real net profit is lower than shown.

Usage: python3 scan.py           (writes data/mev.js + data/mev.json; history stays in data/mev_history/)
"""
import json
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
HIST = DATA / "mev_history"          # one file per UTC day: sandwiches + hourly gas
STATE = DATA / "mev_state.json"      # last scanned block
TOKENS = DATA / "mev_tokens.json"    # cache: pool -> tokens, token -> symbol/decimals

RPCS = ["https://ethereum-rpc.publicnode.com", "https://eth.llamarpc.com"]
MAX_BLOCKS = 900                     # ~3 hours of blocks; one run never takes more than a few minutes
FIRST_RUN_BLOCKS = 300               # ~1 hour on the very first run
BATCH = 10
KEEP_DAYS = 30

SWAP_V2 = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
SWAP_V3 = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
STABLES = {"0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC", "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
           "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI"}


# ---------- JSON-RPC ----------

def post(url, payload, timeout=60):
    req = urllib.request.Request(url, json.dumps(payload).encode(),
                                 {"content-type": "application/json", "user-agent": "market-brief-mev-watch"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def rpc_batch(calls):
    """calls: [(method, params)] -> list of results (None on error). Tries each RPC, retries once."""
    payload = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]
    last = None
    for attempt in range(2):
        for url in RPCS:
            try:
                out = post(url, payload)
                if isinstance(out, dict):  # whole batch rejected
                    raise RuntimeError(out.get("error"))
                by_id = {x["id"]: x for x in out}
                res = [by_id.get(i, {}).get("result") for i in range(len(calls))]
                if all(r is not None for r in res) or attempt:
                    return res
            except Exception as e:  # noqa: BLE001 - network/RPC errors: try the next node
                last = e
        time.sleep(3)
    raise RuntimeError(f"all RPCs failed: {last}")


def rpc(method, params):
    return rpc_batch([(method, params)])[0]


# ---------- token metadata ----------

def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def decode_str(hexdata):
    b = bytes.fromhex((hexdata or "0x")[2:])
    if len(b) >= 96:  # ABI-encoded string
        n = int.from_bytes(b[32:64], "big")
        return b[64:64 + n].decode("utf-8", "replace")
    return b.rstrip(b"\0").decode("utf-8", "replace")  # bytes32 symbol (old tokens like MKR)


def resolve_tokens(pools, cache):
    """Fill cache['pools'][pool] = [token0, token1] and cache['tokens'][addr] = [symbol, decimals]."""
    pc, tc = cache.setdefault("pools", {}), cache.setdefault("tokens", {})
    todo = [p for p in pools if p not in pc]
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        calls = []
        for p in chunk:
            calls += [("eth_call", [{"to": p, "data": "0x0dfe1681"}, "latest"]),   # token0()
                      ("eth_call", [{"to": p, "data": "0xd21220a7"}, "latest"])]   # token1()
        res = rpc_batch(calls)
        for k, p in enumerate(chunk):
            a, b = res[2 * k], res[2 * k + 1]
            if a and b and len(a) >= 66 and len(b) >= 66:
                pc[p] = ["0x" + a[-40:], "0x" + b[-40:]]
    toks = {t for p in pools if p in pc for t in pc[p]} - set(tc)
    toks = list(toks)
    for i in range(0, len(toks), BATCH):
        chunk = toks[i:i + BATCH]
        calls = []
        for t in chunk:
            calls += [("eth_call", [{"to": t, "data": "0x95d89b41"}, "latest"]),   # symbol()
                      ("eth_call", [{"to": t, "data": "0x313ce567"}, "latest"])]   # decimals()
        res = rpc_batch(calls)
        for k, t in enumerate(chunk):
            try:
                sym = decode_str(res[2 * k])[:16] or t[:8]
                dec = int(res[2 * k + 1], 16) if res[2 * k + 1] and res[2 * k + 1] != "0x" else 18
            except ValueError:
                sym, dec = t[:8], 18
            tc[t] = [sym, dec]


# ---------- detection ----------

def s256(word):
    v = int(word, 16)
    return v - (1 << 256) if v >> 255 else v


def pool_swaps(receipts):
    """-> {pool: [swap]} in block order. A swap is one tx's net flow through one pool (pool's view:
    d0 > 0 means token0 went into the pool, i.e. the trader sold token0)."""
    pools = defaultdict(dict)
    for r in receipts:
        if r.get("status") != "0x1":
            continue
        idx = int(r["transactionIndex"], 16)
        for lg in r["logs"]:
            t = lg["topics"]
            if not t or t[0] not in (SWAP_V2, SWAP_V3):
                continue
            d = lg["data"][2:]
            w = [d[i:i + 64] for i in range(0, len(d), 64)]
            if t[0] == SWAP_V2 and len(w) >= 4:
                d0, d1 = int(w[0], 16) - int(w[2], 16), int(w[1], 16) - int(w[3], 16)
            elif t[0] == SWAP_V3 and len(w) >= 2:
                d0, d1 = s256(w[0]), s256(w[1])
            else:
                continue
            s = pools[lg["address"].lower()].setdefault(idx, {
                "i": idx, "hash": r["transactionHash"], "from": r["from"].lower(), "to": (r.get("to") or "").lower(),
                "gas_eth": int(r["gasUsed"], 16) * int(r.get("effectiveGasPrice", "0x0"), 16) / 1e18, "d0": 0, "d1": 0})
            s["d0"] += d0
            s["d1"] += d1
    return {p: sorted(v.values(), key=lambda s: s["i"]) for p, v in pools.items()}


def shared_contracts(receipts):
    """Contracts called by 3+ different wallets in one block are routers/aggregators, not one bot's contract."""
    senders = defaultdict(set)
    for r in receipts:
        senders[(r.get("to") or "").lower()].add(r["from"].lower())
    return {to for to, s in senders.items() if len(s) >= 3}


def find_sandwiches(swaps, shared):
    """Front-run and back-run by the same bot (same sender, or same private contract), opposite directions,
    the back-run selling back roughly what the front-run bought, with at least one other trader in between
    going the same way as the front-run."""
    found, used = [], set()
    for a_k, a in enumerate(swaps):
        if a["i"] in used or a["d0"] == 0:
            continue
        sells0 = a["d0"] > 0
        for c in swaps[a_k + 1:]:
            same_bot = c["from"] == a["from"] or (a["to"] and c["to"] == a["to"] and a["to"] not in shared)
            if not same_bot or (c["d0"] > 0) == sells0 or c["d0"] == 0:
                continue
            # Amount of the "bought" token: front-run received it, back-run sold it back.
            got = -a["d1"] if sells0 else -a["d0"]
            back = c["d1"] if sells0 else c["d0"]
            if got <= 0 or not (0.9 <= back / got <= 1.1):
                break
            victims = [b for b in swaps if a["i"] < b["i"] < c["i"] and (b["d0"] > 0) == sells0
                       and b["from"] not in (a["from"], c["from"]) and (b["to"] != a["to"] or a["to"] in shared)]
            if victims:
                found.append((a, victims, c))
                used.update((a["i"], c["i"]))
            break
    return found


# ---------- main ----------

def eth_price_usd():
    try:
        with urllib.request.urlopen("https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd",
                                    timeout=20) as r:
            return float(json.load(r)["ethereum"]["usd"])
    except Exception:  # noqa: BLE001
        return None


def scan(start, end, eth_usd, cache):
    gas_hours = defaultdict(lambda: {"blocks": 0, "base_gwei": 0.0, "full": 0.0, "txs": 0})
    raw = []
    for b0 in range(start, end + 1, BATCH // 2):
        nums = list(range(b0, min(b0 + BATCH // 2, end + 1)))
        calls = []
        for n in nums:
            calls += [("eth_getBlockByNumber", [hex(n), False]), ("eth_getBlockReceipts", [hex(n)])]
        res = rpc_batch(calls)
        for k, n in enumerate(nums):
            blk, rcpts = res[2 * k], res[2 * k + 1]
            if not blk or rcpts is None:
                continue
            ts = int(blk["timestamp"], 16)
            hour = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:00Z")
            g = gas_hours[hour]
            g["blocks"] += 1
            g["base_gwei"] += int(blk.get("baseFeePerGas", "0x0"), 16) / 1e9
            g["full"] += int(blk["gasUsed"], 16) / max(int(blk["gasLimit"], 16), 1)
            g["txs"] += len(rcpts)
            shared = shared_contracts(rcpts)
            for pool, swaps in pool_swaps(rcpts).items():
                for a, victims, c in find_sandwiches(swaps, shared):
                    raw.append((n, ts, pool, a, victims, c))
        if (b0 - start) % 100 == 0:
            print(f"  block {b0} ({b0 - start}/{end - start + 1}), sandwiches so far: {len(raw)}", flush=True)

    resolve_tokens({r[2] for r in raw}, cache)
    out = []
    for n, ts, pool, a, victims, c in raw:
        toks = cache["pools"].get(pool)
        sells0 = a["d0"] > 0
        # Profit is in the token the bot started with (what it sold in the front-run and got back after).
        start_tok = (toks[0] if sells0 else toks[1]) if toks else None
        delta = (-c["d0"] - a["d0"]) if sells0 else (-c["d1"] - a["d1"])
        sym, dec = cache["tokens"].get(start_tok, ["?", 18]) if start_tok else ["?", 18]
        amount = delta / 10 ** dec
        gross = None
        if start_tok == WETH and eth_usd:
            gross = amount * eth_usd
        elif start_tok in STABLES:
            gross = amount
        gas_usd = (a["gas_eth"] + c["gas_eth"]) * eth_usd if eth_usd else None
        pair = "/".join(cache["tokens"].get(t, ["?"])[0] for t in toks) if toks else pool[:10]
        out.append({
            "block": n, "time": datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds"),
            "pool": pool, "pair": pair, "bot": a["from"] if a["to"] in shared or not a["to"] else a["to"], "bot_eoa": a["from"],
            "front": a["hash"], "back": c["hash"], "victims": [v["hash"] for v in victims],
            "profit_token": sym, "profit_amount": round(amount, 8),
            "gross_usd": round(gross, 2) if gross is not None else None,
            "gas_usd": round(gas_usd, 2) if gas_usd is not None else None,
        })
    return out, gas_hours


def main():
    DATA.mkdir(exist_ok=True)
    HIST.mkdir(exist_ok=True)
    state, cache = load_json(STATE, {}), load_json(TOKENS, {})
    head = int(rpc("eth_blockNumber", []), 16) - 2  # stay a couple of blocks behind the tip (reorgs)
    start = state.get("last_block", head - FIRST_RUN_BLOCKS) + 1
    if head - start + 1 > MAX_BLOCKS:
        print(f"Gap of {head - start + 1} blocks; scanning only the last {MAX_BLOCKS}.")
        start = head - MAX_BLOCKS + 1
    if start > head:
        print("No new blocks.")
        return
    eth_usd = eth_price_usd()
    print(f"Scanning blocks {start}..{head} ({head - start + 1}), ETH ${eth_usd}", flush=True)
    sandwiches, gas_hours = scan(start, head, eth_usd, cache)

    # Merge into per-day history files.
    by_day = defaultdict(lambda: {"sandwiches": [], "gas": {}})
    for s in sandwiches:
        by_day[s["time"][:10]]["sandwiches"].append(s)
    for hour, g in gas_hours.items():
        by_day[hour[:10]]["gas"][hour] = g
    for day, add in by_day.items():
        f = HIST / f"{day}.json"
        d = load_json(f, {"sandwiches": [], "gas": {}})
        seen = {s["front"] for s in d["sandwiches"]}
        d["sandwiches"] += [s for s in add["sandwiches"] if s["front"] not in seen]
        for hour, g in add["gas"].items():  # an hour can be split across two runs: add up the sums
            old = d["gas"].get(hour, {"blocks": 0, "base_gwei": 0.0, "full": 0.0, "txs": 0})
            d["gas"][hour] = {k: old[k] + g[k] for k in old}
        f.write_text(json.dumps(d))
    for f in HIST.glob("*.json"):
        if f.stem < (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d"):
            f.unlink()

    state["last_block"] = head
    state.setdefault("first_scan", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    STATE.write_text(json.dumps(state))
    TOKENS.write_text(json.dumps(cache))
    build_output(eth_usd)
    print(f"Done: {len(sandwiches)} sandwiches in {head - start + 1} blocks.")


def build_output(eth_usd):
    """Summarise history into data/mev.js for the page."""
    now = datetime.now(timezone.utc)
    days = {f.stem: load_json(f, {}) for f in sorted(HIST.glob("*.json"))}
    all_s = [s for d in days.values() for s in d.get("sandwiches", [])]
    cutoff = (now - timedelta(hours=24)).isoformat()
    last24 = sorted((s for s in all_s if s["time"] >= cutoff), key=lambda s: s["time"], reverse=True)

    def net(s):
        return None if s["gross_usd"] is None else s["gross_usd"] - (s["gas_usd"] or 0)

    def bots(items):
        agg = {}
        for s in items:
            b = agg.setdefault(s["bot"], {"bot": s["bot"], "count": 0, "victims": 0, "gross_usd": 0.0,
                                          "gas_usd": 0.0, "unpriced": 0})
            b["count"] += 1
            b["victims"] += len(s["victims"])
            if s["gross_usd"] is None:
                b["unpriced"] += 1
            else:
                b["gross_usd"] += s["gross_usd"]
                b["gas_usd"] += s["gas_usd"] or 0
        rows = sorted(agg.values(), key=lambda b: b["gross_usd"] - b["gas_usd"], reverse=True)
        for b in rows:
            b["gross_usd"], b["gas_usd"] = round(b["gross_usd"], 2), round(b["gas_usd"], 2)
        return rows

    daily = []
    for day, d in days.items():
        ss = d.get("sandwiches", [])
        priced = [s for s in ss if s["gross_usd"] is not None]
        g = d.get("gas", {})
        nb = sum(h["blocks"] for h in g.values())
        daily.append({"day": day, "count": len(ss), "victims": sum(len(s["victims"]) for s in ss),
                      "gross_usd": round(sum(s["gross_usd"] for s in priced), 2),
                      "net_usd": round(sum(net(s) for s in priced), 2), "blocks": nb, "bots": len({s["bot"] for s in ss})})

    gas = []
    for d in days.values():
        for hour, h in d.get("gas", {}).items():
            if h["blocks"] and hour >= (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:00Z"):
                gas.append({"hour": hour, "base_gwei": round(h["base_gwei"] / h["blocks"], 3),
                            "full_pct": round(100 * h["full"] / h["blocks"], 1),
                            "txs_per_block": round(h["txs"] / h["blocks"], 1), "blocks": h["blocks"]})
    gas.sort(key=lambda h: h["hour"])

    week_cut = (now - timedelta(days=7)).isoformat()
    biggest = max((s for s in last24 if s["gross_usd"] is not None), key=lambda s: s["gross_usd"], default=None)
    out = {
        "generated_at": now.isoformat(timespec="seconds"), "eth_usd": eth_usd,
        "first_scan": load_json(STATE, {}).get("first_scan"),
        "blocks_24h": sum(h["blocks"] for h in gas if h["hour"] >= (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:00Z")),
        "recent": last24[:150], "count_24h": len(last24), "example": biggest,
        "bots_24h": bots(last24)[:15], "bots_7d": bots([s for s in all_s if s["time"] >= week_cut])[:15],
        "daily": daily, "gas": gas,
    }
    (DATA / "mev.json").write_text(json.dumps(out, indent=1))
    (DATA / "mev.js").write_text("window.MEV_DATA = " + json.dumps(out) + ";\n")


if __name__ == "__main__":
    main()
