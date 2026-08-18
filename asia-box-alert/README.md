# 亚盘盒子监测（PC 版）

Windows 桌面小工具：实时拉黄金现货价，按 **A 震荡 / B 单边** 判断，到进场点弹系统提醒。

不自动下单。价格来自公开现货接口，和 MT5 可能差几美元，**盒子建议对照你图上的 ASIA_H / ASIA_L 手动填一次**。

## 怎么运行（双击，不用打命令）

1. 安装 [Python 3.10+](https://www.python.org/downloads/)，勾选 **Add python.exe to PATH**（只需一次）
2. 打开 `asia-box-alert` 文件夹
3. **双击 `open.vbs`**（推荐，无黑窗口）

   若没反应，改双击 **`launch.bat`** 或 **`run.bat`**（有黑窗口但最稳）

不要双击 `python.exe`，也不要双击 `app.py`。

想放在桌面：再双击一次 `MakeDesktopShortcut.bat`，桌面会多出 **AsiaBox** 图标。

## 窗口变成 Python 的 >>> 

不要在 `>>>` 里乱输入。先粘贴这三行启动（把路径改成你的文件夹）：

```python
import os, runpy
os.chdir(r"D:\download\FPS-final-cursor-asia-box-scalp-playbook-dbcf\asia-box-alert")
runpy.run_path("app.py", run_name="__main__")
```

或关掉窗口后，重新双击修好的 `run.bat`。

不要关那个黑窗口。新的 `run.bat` 结束时会停住，并写出原因。

常见原因：
- 没装 Python，或没勾选 **Add python.exe to PATH**
- 装的是微软商店空壳，请改用 python.org 正式版
- 没在 `asia-box-alert` 文件夹里运行（外层目录双击会找不到 app.py）

若仍失败，把黑窗口全文，或同目录 `error.log` 发给我。

```bat
cd asia-box-alert
pip install -r requirements.txt
python app.py
```

只看一次当前判断：

```bat
python app.py --once
```

## 界面会显示什么

- **时段**（锁盒 / 可交易 / 01:00停新单 / 01:45平仓）
- **盒子** H/L、RANGE、上沿/下沿/中间位置
- **ADX**、**RSI(M15)**、**M15 收盘**、**日型**（震荡 A / 单边 B）
- **大数据日历**（CPI/NFP/FOMC 等，前后 30 分钟禁做并弹提醒）
- **推荐入场** 时 Windows 弹窗（A 上沿空/下沿多，B 回踩）

## 自测

```bat
py -3 app.py --test
py -3 app.py --test --popup
```

或在窗口里点 **运行自测**。

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
