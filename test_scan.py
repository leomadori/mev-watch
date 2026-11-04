"""Run: python3 test_scan.py"""
from scan import is_bot, paid_to_builder

M = "0xb0"
call = lambda addr, to, wei, kind="call", **kw: {"type": "call", "traceAddress": addr,
                                                 "action": {"callType": kind, "to": to, "value": hex(wei)}, **kw}
assert paid_to_builder([call([], "0xbot", 0), call([0], M, 10**18)], M) == 1
assert paid_to_builder([call([0], "0xother", 10**18)], M) == 0
assert paid_to_builder([call([0], M, 10**18, "delegatecall")], M) == 0
# A reverted call undoes everything under it, not just itself.
assert paid_to_builder([call([0], "0xbot", 0, error="Reverted"), call([0, 1], M, 10**18), call([1], M, 10**18)], M) == 1
assert is_bot({"bot_eoa": "a", "back_eoa": "a"})
assert not is_bot({"bot_eoa": "a", "back_eoa": "b", "builder_eth": 0})
assert is_bot({"bot_eoa": "a", "back_eoa": "b", "builder_eth": 0.1})
print("ok")
