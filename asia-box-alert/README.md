# 亚盘盒子监测（PC 版）

Windows 桌面小工具：实时拉黄金现货价，按 **A 震荡 / B 单边** 判断，到进场点弹系统提醒。

不自动下单。价格来自公开现货接口，和 MT5 可能差几美元，**盒子建议对照你图上的 ASIA_H / ASIA_L 手动填一次**。

## 怎么运行

1. 安装 [Python 3.10+](https://www.python.org/downloads/)，勾选 **Add python.exe to PATH**
2. 双击 `run.bat`
3. 窗口保持打开；到点会弹 Windows 通知 + 响一声

也可命令行：

```bat
cd asia-box-alert
pip install -r requirements.txt
python app.py
```

只看一次当前判断：

```bat
python app.py --once
```

## 它会提醒什么

| 情况 | 提醒 |
|------|------|
| 价格进上沿 25%，ADX 显示震荡 | A 可挂空 + SL/TP/手数 |
| 价格进下沿 25%，震荡 | A 可挂多 |
| 中间 50% | 不弹（空仓） |
| M15 破上沿 / ADX>28 向上 | 切 B，等回踩 ASIA_H，不要追 |
| 价格回到上沿附近 | B 做多限价到了 |
| 01:00 后 | 停新单；01:45 提醒该平仓 |

同一信号大约 3 分钟内不重复弹。

## 手动盒子

打开后填 **ASIA_H / ASIA_L** → 点 **保存盒子**。  
这个数字以你 MT5 为准，比自动估算更准。
