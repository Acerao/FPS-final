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
        ("无ADX仍按A下沿", 4375, None, 4378, "a_buy"),
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

    class _Bar:
        def __init__(self, o: float, h: float, l: float, c: float) -> None:
            self.open = o
            self.high = h
            self.low = l
            self.close = c

    print("\n=== 高胜率确认K自测 ===")
    m15_confirm_long = [
        _Bar(4408, 4410, 4404, 4406),  # prev bearish
        _Bar(4406, 4411, 4405, 4410),  # closed bullish engulf
        _Bar(4410, 4412, 4409, 4411),  # forming
    ]
    m15_no_confirm = [
        _Bar(4408, 4410, 4404, 4409),
        _Bar(4409, 4410, 4407, 4408),
        _Bar(4408, 4409, 4407, 4408),
    ]
    sig = evaluate(4375, box, AdxState(18, 20, 18), 4378, now=noon, recent_m15=m15_no_confirm, profile="high_winrate")
    if sig.key == "hwr_wait_a_long_confirm":
        print("[OK] 高胜率版无确认K不入场")
        ok += 1
    else:
        print(f"[FAIL] 期望等待确认，得到 {sig.key}")
        fail += 1
    sig = evaluate(
        4375,
        box,
        AdxState(18, 20, 18),
        4378,
        now=noon,
        recent_m15=m15_confirm_long,
        profile="high_winrate",
    )
    if sig.key == "a_buy":
        print("[OK] 高胜率版有确认K才入场")
        ok += 1
    else:
        print(f"[FAIL] 期望 a_buy，得到 {sig.key}")
        fail += 1

    print("\n=== 冲刺版自测 ===")
    sig = evaluate(4375, box, AdxState(18, 20, 18), 4378, now=noon, profile="sprint", lot=0.05)
    if sig.key == "sprint_skip_a":
        print("[OK] 冲刺版震荡日不做 A")
        ok += 1
    else:
        print(f"[FAIL] 冲刺版应跳过 A，得到 {sig.key}")
        fail += 1
    sig = evaluate(4416, box, AdxState(35, 40, 10), 4420, now=noon, recent_m15=m15_confirm_long, profile="sprint", lot=0.05)
    if sig.key == "b_long" and "0.05" in sig.message and "$75" in sig.message:
        print("[OK] 冲刺版 B 回踩带 0.05 手金额")
        ok += 1
    else:
        print(f"[FAIL] 冲刺 B 期望带手数金额，得到 {sig.key} {sig.message}")
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

    print("\n=== 本地K线积累自测 ===")
    from datetime import timedelta
    from spot_history import SpotHistory
    from pathlib import Path
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "ticks.json"
    hist = SpotHistory(tmp)
    base = datetime(2026, 8, 17, 10, 0, tzinfo=BEIJING)
    for i in range(20):
        hist.add(4400.0 + i * 0.1, base + timedelta(minutes=i * 2))
    m5 = hist.m5_bars()
    if len(m5) >= 3:
        print(f"[OK] 现货采样可聚合 M5 ({len(m5)} 根)")
        ok += 1
    else:
        print(f"[FAIL] M5 聚合不足: {len(m5)}")
        fail += 1

    print("\n=== 报价回退自测 ===")
    from gold_feed import save_spot_cache, load_spot_cache

    save_spot_cache(4400.0, "test")
    cached = load_spot_cache()
    if cached and abs(cached[0] - 4400.0) < 0.01:
        print("[OK] 报价磁盘缓存可读")
        ok += 1
    else:
        print("[FAIL] 报价缓存")
        fail += 1

    print("\n=== 国内报价源自测 ===")
    from gold_feed import _parse_js_quote, fetch_spot

    sample = 'var hq_str_hf_XAU="4397.36,4416.5,4397.36,4397.71,4435.95,4394.18";\n'
    if abs(_parse_js_quote(sample) - 4397.36) < 0.01:
        print("[OK] 新浪报价格式解析")
        ok += 1
    else:
        print("[FAIL] 新浪解析")
        fail += 1
    try:
        price, src = fetch_spot()
        cn = any(k in src for k in ("新浪", "腾讯", "东方", "小渡"))
        print(f"[{'OK' if cn or price > 500 else 'FAIL'}] 现货 {price:.2f} ← {src}")
        if price > 500:
            ok += 1
        else:
            fail += 1
    except Exception as exc:
        print(f"[SKIP] 现货联网自测跳过: {exc}")

    print("\n=== K线解析自测 ===")
    from gold_feed import _loads_payload, aggregate_bars, Bar

    bom_json = "\ufeff{\"ok\": true, \"n\": 1}"
    if _loads_payload(bom_json).get("ok") is True:
        print("[OK] UTF-8 BOM JSON 可解析")
        ok += 1
    else:
        print("[FAIL] BOM JSON")
        fail += 1
    jsonp = 'var k=({"minLine_1d":[1]});'
    if _loads_payload(jsonp).get("minLine_1d") == [1]:
        print("[OK] JSONP 可解析")
        ok += 1
    else:
        print("[FAIL] JSONP")
        fail += 1
    sample_bars = [
        Bar(ts=datetime(2026, 8, 18, 10, m, tzinfo=BEIJING), open=4400, high=4401, low=4399, close=4400 + m)
        for m in range(0, 45)
    ]
    m15 = aggregate_bars(sample_bars, 15)
    if len(m15) == 3:
        print("[OK] 分钟线可合成 M15")
        ok += 1
    else:
        print(f"[FAIL] M15 合成 {len(m15)}")
        fail += 1
    try:
        from bar_source import get_indicator_bars

        pack = get_indicator_bars()
        if pack.bars and len(pack.bars) >= 5:
            print(f"[OK] 在线K线 {pack.source} {len(pack.bars)}根 ADX周期={pack.adx_tf}")
            ok += 1
        else:
            print(f"[FAIL] 在线K线不足: {pack.source} {pack.note}")
            fail += 1
        from strategy import compute_adx as _adx, compute_rsi as _rsi

        if pack.adx_bars:
            st = _adx(
                [b.high for b in pack.adx_bars],
                [b.low for b in pack.adx_bars],
                [b.close for b in pack.adx_bars],
            )
            if st is not None:
                print(f"[OK] ADX({pack.adx_tf})={st.adx:.1f}")
                ok += 1
            else:
                print("[FAIL] 有ADX序列却算不出")
                fail += 1
        rsi_v = _rsi([b.close for b in pack.bars]) if pack.bars else None
        if rsi_v is not None:
            print(f"[OK] RSI={rsi_v:.1f}（入场不看RSI）")
            ok += 1
        else:
            print("[FAIL] RSI 仍为空")
            fail += 1
    except Exception as exc:
        print(f"[SKIP] 在线K线: {exc}")

    print("\n=== 本机目录同步自测 ===")
    from sync_local import DEFAULT_MIRROR, KEEP_NAMES, should_copy, sync_to_mirror
    from pathlib import Path

    if str(DEFAULT_MIRROR).replace("/", "\\").lower().endswith("gold\\asia-box-alert"):
        print("[OK] 默认镜像路径 E:\\gold\\asia-box-alert")
        ok += 1
    else:
        print(f"[FAIL] 镜像路径不对: {DEFAULT_MIRROR}")
        fail += 1
    if not should_copy(Path("config.json")) and should_copy(Path("app.py")):
        print("[OK] 同步会保留 config.json、会复制 app.py")
        ok += 1
    else:
        print("[FAIL] 同步保留规则")
        fail += 1
    if "price_ticks.json" in KEEP_NAMES:
        print("[OK] 不会覆盖本地采样")
        ok += 1
    else:
        print("[FAIL] KEEP_NAMES 缺 price_ticks.json")
        fail += 1
    ok_sync, msg = sync_to_mirror()
    if (not ok_sync) and "非 Windows" in msg:
        print("[OK] 云端不会误建 E:\\gold 目录")
        ok += 1
    elif ok_sync:
        print(f"[OK] {msg}")
        ok += 1
    else:
        print(f"[FAIL] {msg}")
        fail += 1

    print("\n=== 等距网格自测 ===")
    from scale_grid import GridState, evaluate_grid

    grid_now = datetime(2026, 8, 17, 16, 0, tzinfo=BEIJING)
    st = GridState(side="long", anchor=4400, layers=2)
    sig = evaluate_grid(4384, st, now=grid_now)
    if sig.key == "grid_add":
        print("[OK] 跌到下一层提醒加仓")
        ok += 1
    else:
        print(f"[FAIL] 应加仓，得到 {sig.key}")
        fail += 1
    st3 = GridState(side="long", anchor=4400, layers=3)
    sig = evaluate_grid(4398, st3, now=grid_now)
    if sig.key == "grid_close_all":
        print("[OK] 回弹到均价上方提醒全平")
        ok += 1
    else:
        print(f"[FAIL] 应全平，得到 {sig.key}")
        fail += 1
    sig = evaluate_grid(4370, st3, now=grid_now)
    if sig.key == "grid_stop_all":
        print("[OK] 不回弹触及硬止损")
        ok += 1
    else:
        print(f"[FAIL] 应止损，得到 {sig.key}")
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
