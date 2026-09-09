---
name: asia-box-scalp
description: >-
  The5ers XAUUSD 亚盘盒子日内波段（Asia Box）。用户说亚盘盒子、黄金策略、
  ASIA_H、ASIA_L、怎么买/怎么卖、守门、复盘时使用。持仓可扛 $10–20 波动，
  不是超短。只给可执行规则，不自动下单，不承诺收益。
---

# 亚盘盒子（Asia Box）· 日内波段

评估期防守版。先读 [PLAYBOOK.md](PLAYBOOK.md)。

## 何时用

- 用户给了 `ASIA_H` / `ASIA_L` 或今日黄金图
- 问现在能不能买/卖、SL/TP/手数
- 说「按盒子做」「黄金策略」
- 说网格、马丁、跌了加仓、回弹全平 → 读 [PLAYBOOK_GRID.md](PLAYBOOK_GRID.md)，评估期只用等距网格，禁止翻倍马丁
- 说翻倍、百倍、对冲、B站/油管策略视频 → 读 [PLAYBOOK_VIDEOS.md](PLAYBOOK_VIDEOS.md)：只借结构单笔，禁止对冲/马丁/无硬止损
- 问能赚多少、多久到 $1000、去年前年走势 → 读 [BACKTEST_HWR.md](BACKTEST_HWR.md)
- 问短期 1k、加大手数、冲刺 → 读 [PLAYBOOK_SPRINT.md](PLAYBOOK_SPRINT.md)，用软件 `asia_box_sprint` + 手数 0.05
- 说画线、压力位、支撑位、突破、8小时、大熊讲黄金 → 读 [PLAYBOOK_LINES.md](PLAYBOOK_LINES.md)：H8 定方向，盒子只做同向 B

## 执行顺序

1. 盒子未锁（北京未到 14:30）→ 只标记，不按 A 下单。
2. 计算三区。价格在中间区 → 明确说「空仓」。
3. 价格仍在盒子内且 ADX<22 → 策略 A（上沿空、下沿多，限价）。默认 **SL $15 / TP $12**。
4. **M15 收盘破 ASIA_H/ASIA_L 或 ADX>28** → 撤掉 A，改策略 B：回踩突破位顺势限价，不追第一波。
5. 给出：A空仓 / 挂A限价 / 切B / 挂B回踩限价 + 价格 + SL + TP + 手数。
6. A 止损但未破盒子 → 当天停。A 止损且已破盒子 → 允许 1 笔 B。
7. 硬刹车：无 SL 不下单、连亏 2 停、日损 -$100 停、**01:45 北京前平仓**。

## 用户本机目录（务必同步）

用户日常运行路径是 **`E:\gold\asia-box-alert`**。云端写不到这块盘。

改监测软件时必须同时：

1. 改仓库里的 `asia-box-alert/`
2. 用 `sync_local.py` 尝试写入 `E:\gold\asia-box-alert`（本机 Windows agent 才能成功）
3. 保证 `updater.py` 仍能从 GitHub 拉最新并镜像到该路径
4. 不要覆盖用户的 `config.json` / `price_ticks.json` / `last_spot.json`

用户双击 `run.bat` 或点界面「更新程序」会自动同步，无需每次 git pull。

## 禁止

- 不代替用户下单
- 不在中间区找「差不多能做」的理由
- 不把本策略说成某位华尔街大佬的原版秘籍
- 不要再改回 5–30 分钟、$2–4 止损的超短，除非用户明确要求
- 不要再要求 22:30 必须睡前平仓；平仓点以 **01:45** 为准
- 不要再把 RSI 当成下单必要条件；默认用盒子边缘限价单
- 单边行情不要追第一波，等回踩 ASIA_H / ASIA_L 再挂 B
- 不要把「500U 翻 22 倍 / 1000U 翻 100 倍 / 对冲 10 倍」搬进评估账户；那些靠杠杆和扛单，不是盒子规则
