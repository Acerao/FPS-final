"""Self-test for strategy, news blackout, and alert plumbing."""

from __future__ import annotations

from datetime import datetime

from dashboard import build_dashboard, ENTRY_KEYS
from news_calendar import NewsEvent, NewsStatus, get_news_status
from strategy import AdxState, Box, evaluate
from tzutil import BEIJING


def run_selftest(popup: bool = False) -> int:
    ok = 0
    fail = 0
    box = Box(high=4416, low=4368)
    noon = datetime(2026, 8, 17, 15, 0, tzinfo=BEIJING)

    cases = [
        ("A下沿做多", 4375, AdxState(18, 20, 18), 4378, "a_buy"),
        ("A上沿做空", 4410, AdxState(19, 18, 22), 4408, "a_sell"),
        ("A中间空仓", 4395, AdxState(18, 20, 18), 4390, "a_mid"),
        ("B回踩多", 4416, AdxState(35, 40, 10), 4420, "b_long"),
        ("B不追多", 4430, AdxState(35, 40, 10), 4425, "b_no_chase_long"),
    ]
    print("=== 策略信号自测 ===")
    for name, price, adx, m15, expect_key in cases:
        sig = evaluate(price, box, adx, m15, now=noon)
        passed = sig.key == expect_key
        mark = "OK" if passed else "FAIL"
        print(f"[{mark}] {name}: {sig.key} ({sig.title})")
        if passed:
            ok += 1
        else:
            fail += 1

    print("\n=== 大数据禁做自测 ===")
    fake_news = NewsStatus(
        in_blackout=True,
        today_events=[NewsEvent("CPI", noon, "High")],
        active=NewsEvent("CPI", noon, "High"),
        next_event=None,
        summary="禁做测试",
        detail="模拟大数据时段",
    )
    sig = evaluate(4375, box, AdxState(18, 20, 18), 4378, now=noon, news=fake_news)
    if sig.key == "news_blackout":
        print("[OK] 大数据时段拦截入场信号")
        ok += 1
    else:
        print(f"[FAIL] 应拦截却得到 {sig.key}")
        fail += 1

    dash = build_dashboard(4375, box, "测试", AdxState(18, 20, 18), 33.0, 4378, fake_news, noon)
    if not dash.entry_ok:
        print("[OK] Dashboard 标记 entry_ok=False")
        ok += 1
    else:
        print("[FAIL] Dashboard 应禁止入场")
        fail += 1

    print("\n=== 入场提醒键自测 ===")
    for key in ENTRY_KEYS:
        print(f"  会弹提醒: {key}")

    if popup:
        from alerts import popup_alert

        popup_alert("自测提醒", "自测弹窗：如果你看到这条，说明提醒通道正常。", parent=None)
        print("[OK] 已发送测试弹窗")
        ok += 1

    print(f"\n结果: {ok} 通过, {fail} 失败")
    return 1 if fail else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selftest("--popup" in sys.argv))
