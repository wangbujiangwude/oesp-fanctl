# OESP 风扇助手 · oesp-fanctl

> 为 **OES Plus（Amlogic S922X）刷飞牛 fnOS** 打造的智能温控风扇管理应用。
> **安装即用，零配置** —— 装上就能让风扇按温度自动启停。

[![Platform](https://img.shields.io/badge/platform-arm64-blue)]()
[![License](https://img.shields.io/badge/license-MIT-green)]()
[![Version](https://img.shields.io/badge/version-1.15-orange)]()

---

## 目录

- [为什么需要它](#为什么需要它)
- [核心特性](#核心特性)
- [安装](#安装)
- [使用](#使用)
- [硬件说明](#硬件说明)
- [兼容性](#兼容性)
- [相比 1.6 原版的改造](#相比-16-原版的改造)
- [仓库结构](#仓库结构)
- [自行构建 FPK](#自行构建-fpk)
- [常见问题](#常见问题)
- [许可](#许可)

---

## 为什么需要它

OES Plus 刷飞牛 fnOS 之后，风扇控制有三个绕不过去的坑：

1. **社区版 1.6 在 OESP 上装不上** —— 安装钩子硬性检查内核节点 `gpio-fan/hwmon/hwmonX/fan1_target`，而 OESP 的 `gpio_fan.ko` 默认不加载，节点不存在 → 安装直接失败。

2. **就算装上了，风扇也不受软件控制** —— `gpio_fan` 驱动加载后会把自己注册成内核 thermal cooling_device，内核 `step_wise` 调控器会在 CPU 温度 < 60℃ 时，约 1 秒内把写进 `fan1_target` 的值覆盖回 0。原版靠「每 2 秒重写一次」对抗，实测**打不过**。

3. **引脚会漂移** —— 厂商在 fnOS 1.2.0701 中把设备树的风扇 GPIO 从 **46 改成了 45**，所有写死引脚号的脚本在系统更新后集体失效。

本项目的解法：**绕开内核热框架，直接控制 GPIO**。

---

## 核心特性

- 🔧 **GPIO 直控 · 安装即用**
  卸载内核 `gpio_fan` 驱动，改用 `gpioset` 直接控制引脚电平，完全不受内核调控器干扰。

- 📍 **引脚自动探测**
  启动时从设备树 `gpio-fan/gpios` 实时读取引脚号（兜底 45），厂商以后再改 DTB 也不会失效。

- 🧹 **自动接管冲突服务**
  安装后自动停用并禁用 `fanctl.service`、`pwm-fancontrol.service`、`fan-fullspeed.service`，并清理残留进程。

- 🔀 **三种运行模式（1.11 新增，1.14 起网页点击即生效）**
  - **自动温控**（默认）：按 CPU / 硬盘温度启停
  - **常开**：风扇一直运行，忽略温度判定 —— 适合夏季高温、硬盘密集或追求稳定的场景
  - **常关**：风扇一直停止，忽略温度判定 —— 适合夜间静音（⚠ 此模式下过热紧急保护也不触发）

  网页里点一下即生效（**无需再点保存按钮**），或命令行 `oesp-fanctl mode on`，守护进程 2 秒内应用。

- 🌡️ **CPU 智能温控**
  高于「CPU 开启温度」自动开，低于「CPU 关闭温度」自动关；带迟滞防抖（连续 N 次超温才启动）与最短运行时间保护（默认 60 秒），不频繁启停。

- 💨 **硬盘定时通风**
  任一硬盘达到「硬盘开启温度」即开启风扇，运行设定时长（30–1800 秒可调）后自动关闭；关闭后经停机间歇（默认 60 秒）再次测温，若仍超温则开启新一轮，循环至降温。

- 🛡️ **紧急兜底保护**
  CPU ≥ 70℃ 或任一硬盘 ≥ 55℃ 时强制开启风扇。

- 📊 **硬件实时监控**
  CPU / GPU / 三个盘位硬盘（sda·sdb·sdc）温度一屏看完，12 小时历史曲线（每分钟采样一点）。

- ⚙️ **在线调参（带服务端校验）**
  网页内直接调整温度阈值与通风时长，改完即时生效，无需命令行。
  提交时会校验「CPU 开启温度必须高于关闭温度」，写反了直接拒绝，不会留下反直觉配置。

- ⌨️ **命令行工具（1.15 新增）**
  安装时自动创建软链接，SSH 里直接可用：
  - `oesp-fanctl status` 查看状态 · `mode on|off|auto` 切换模式 · `set TEMP_ON 40` 改阈值
  - `fanlog` 实时温度监控，替代旧的 `tail -f /var/log/fan/fanctl.log`
  - `set` 命令带**备份 + 交叉校验 + 回滚**：参数非法或阈值冲突时自动还原，不会写坏配置
  - 改配置时保留行尾注释，不会把 `TEMP_ON=38  # CPU 高于此温度开风扇` 削成一行裸值

- ↩️ **卸载可还原**
  提供 `restore` 流程：结束 `gpioset` → 重新加载 `gpio_fan` → 恢复厂商 60℃ 内核温控 → 重启 `pwm-fancontrol.service`。卸掉不留尾巴。

---

## 安装

### 环境要求

- 硬件：**OES Plus**（Amlogic S922X / meson-g12b）
- 系统：**飞牛 fnOS**（ARM 版）

### 方式一：应用中心（推荐）

应用中心 → **手动安装** → 选择 `release/oesp-fanctl_1.15_arm.fpk`

### 方式二：命令行

```bash
appcenter-cli install-fpk /path/to/oesp-fanctl_1.15_arm.fpk
```

### 安装后自动完成的事

1. 卸载内核 `gpio_fan` 驱动，释放 GPIO
2. 从设备树读取风扇引脚号
3. 停用并禁用冲突服务，清理残留进程
4. 启动温控守护进程，`gpioset` 常驻持有引脚
5. 加载 `drivetemp` 模块（用于读取硬盘温度）
6. 创建命令行软链接 `/usr/local/bin/oesp-fanctl`、`/usr/local/bin/fanlog`

**无需任何手动配置，风扇即刻按温度自动启停。**

---

## 使用

### 查看状态

```bash
# 一次性快照
oesp-fanctl status

# 实时监控（替代旧的 tail -f /var/log/fan/fanctl.log）
fanlog
fanlog 1            # 1 秒间隔
fanlog -f 3         # 追加写入 /var/log/fan/fanctl.log

# 查看运行日志
oesp-fanctl log
```

输出示例：

```
===== oesp-fanctl 状态 =====
控制后端:  gpio 直控（gpiochip0 line 45）
风扇状态:  开启  (0=关, 1=开)
运行模式:  常开
CPU 开启/关闭阈值:  38 / 35 ℃
守护进程:  运行中
Web 监控:  飞牛桌面点「OESP风扇助手」图标（网关 /app/oesp-fanctl）
```

### Web 监控页

在飞牛桌面点「**OESP风扇助手**」图标即可打开，也可直接访问：

```
http://<NAS地址>/app/oesp-fanctl
```

含实时温度、风扇状态、12 小时历史曲线，以及在线调参面板与运行模式切换。

### 手动测试

```bash
oesp-fanctl test on      # 强制开风扇
oesp-fanctl test off     # 强制关风扇
```

### 调整阈值

网页内直接改，或编辑配置文件：

```bash
vi /vol1/@appconf/oesp-fanctl/oesp-fanctl.conf
```

#### 切换运行模式

```bash
oesp-fanctl mode on      # 常开：风扇一直运行
oesp-fanctl mode auto    # 自动温控（默认）
oesp-fanctl mode off     # 常关：风扇一直停止
oesp-fanctl set mode on  # 等价写法
```

也可以直接在 Web 监控页的「风扇运行模式」里点选，**点击即生效**，无需再点保存按钮；守护进程 2 秒内应用。

| 配置项 | 说明 | 默认 |
|---|---|---|
| `FAN_MODE` | 运行模式：`auto` 自动温控 / `on` 常开 / `off` 常关 | auto |
| `TEMP_ON` | CPU 开启温度（℃） | 38 |
| `TEMP_OFF` | CPU 关闭温度（℃） | 35 |
| `HDD_ON_TEMP` | 硬盘通风开启温度（℃） | 50 |
| `HDD_RUN_SECONDS` | 硬盘通风运行时长（秒） | 60 |
| `HDD_COOLDOWN` | 通风后停机间歇（秒） | 60 |
| `EMERGENCY_TEMP` | CPU 紧急强制开启温度（℃） | 70 |
| `DISK_EMERGENCY_TEMP` | 硬盘紧急强制开启温度（℃） | 55 |
| `INTERVAL` | 检测周期（秒） | 2 |
| `STABLE_COUNT` | 连续 N 次超温才启动 | 3 |
| `MIN_RUN` | 风扇最短运行时间（秒） | 60 |

> 💡 想要更安静？把阈值调高即可，比如 `45/38`，或厂商原设计的 `60/55`。

### 卸载

应用中心卸载即可。卸载钩子会自动执行 `restore`，恢复系统原本的风扇控制方式。

---

## 硬件说明

> ⚠️ **OESP 内置风扇是 GPIO 二态开关风扇，仅支持「开 / 关」，不支持 PWM 调速。**

该风扇为 2pin 直连供电，引脚只有静态电平输出，没有 PWM 波形。转速由供电电压决定，**任何软件都无法改变转速** —— 这是硬件限制。

---

## 兼容性

| 项目 | 说明 |
|---|---|
| 设备 | OES Plus（Amlogic S922X） |
| 系统 | 飞牛 fnOS ARM 版（实测 1.2.0701） |
| GPIO 控制器 | `gpiochip0`（`periphs-banks`，base 512） |
| 风扇引脚 | line 45（`GPIOC_4`，ACTIVE_HIGH），从设备树自动读取 |
| 架构 | arm64 |

> 其他 S922X 设备理论上也可用，但引脚号与设备树节点名需匹配；软件会自动从设备树读取，读不到时回退 45。

---

## 相比 1.6 原版的改造

| 维度 | 1.6 原版 | 本版 |
|---|---|---|
| **控制通路** | 写 hwmon `fan1_target` | `modprobe -r gpio_fan` + `gpioset` 直控 GPIO |
| **内核干扰** | 会被 step_wise 在 <60℃ 覆盖 | 完全不经过内核热框架 |
| **引脚来源** | 依赖驱动自动探测 | 从设备树动态读取，兜底 45 |
| **冲突服务** | 只停 2 个 | 追加接管 `fanctl.service` |
| **卸载恢复** | 仅重启 pwm-fancontrol | `restore`：还原厂商 60℃ 内核温控 |
| **默认阈值** | 45/38 | 38/35（网页可调） |
| **命令行** | 无 | 安装即建软链接：`oesp-fanctl` / `fanlog`（1.15） |
| **配置校验** | 无 | Web 与 CLI 均做阈值交叉校验，写错自动拒绝 / 回滚（1.15） |

### 修复的 BUG 清单

| # | 问题 | 原因 | 修复 |
|---|---|---|---|
| 1 | 安装直接失败 | 钩子硬检查 `fan1_target`，而 `gpio_fan` 默认不加载 | 不再依赖该节点，走 GPIO 直控 |
| 2 | 风扇不受软件控制 | `fan1_target` 被内核 step_wise 在 <60℃ 覆盖 | 卸载 `gpio_fan`，改用 `gpioset` 直控 |
| 3 | 引脚变更后失效 | 厂商把 DTB 引脚 46 → 45 | 引脚从设备树动态读取 |
| 4 | 与已有风扇服务抢控制权 | 未接管 `fanctl.service` | 安装时自动停用并清理 |
| 5 | `status` 风扇状态恒显示"关闭" | `read_fan()` 只读守护进程内存变量 | 回退读状态文件 + `gpioget` 实读兜底 |
| 6 | 「服务状态」提示误导 | 应用由应用中心托管，无独立 service | 改为「守护进程: 运行中 / 未运行」 |
| 7 | 状态文件解析失败 | state 为多行格式，原正则要求行首有前置字符 | 改用 `grep -o 'fan_state=[0-9]*'` |
| 8 | **温度未到关闭阈值就提前停风扇** | 用 `${temp_f%.*}` 截断小数：35.9℃ 被当成 35℃ | 改用毫摄氏度原始值精确比较；日志温度与判定温度取自同一次读数 |
| 9 | **SSH 里执行 `set`/`mode` 报「配置文件不存在」** | 命令行执行时没有 appcenter 注入的 `OESP_FANCTL_CONF`，路径回落到 `/etc/oesp-fanctl.conf` | 新增 `resolve_paths()` 自动发现 conf/state/log/history |
| 10 | **紧急保护退出瞬间风扇闪停一个周期** | 紧急标志一退，CPU 温控的防抖计数还没攒够 | 进入紧急保护时同步置位 CPU 温控路径 |
| 11 | **Web 里点「常开 / 常关」后还要再点一次保存才生效** | 模式按钮只改前端高亮，必须靠表单提交 | 按钮改为点击即发 `POST /api/config`，只提交 `FAN_MODE`（不动温度阈值） |
| 12 | **Web 能提交「开启温度低于关闭温度」的反直觉配置** | 服务端只校验字段类型，不做阈值交叉判断 | `POST /api/config` 合并「本次提交 + 现有配置」后校验，违反则 400 拒绝 |
| 13 | **CLI `set` 无校验，且会把行尾注释删掉** | `sed 's/^TEMP_ON=.*/.../'` 通配吞掉注释，且无阈值交叉校验 | 加 `mktemp` 备份 + 校验 + 失败回滚；sed 改为 `s/^TEMP_ON=[^[:space:]]*/.../` 保留注释 |
| 14 | **重启后残留多个 `fanweb.py` 进程** | 每次启动都 `nohup` 起新进程，旧的没清理（`-9` 强杀时 EXIT trap 不执行） | 守护进程与 `cmd/main` 的 start/stop 分支均先 `pkill -f fanweb\.py` 做单例保护 |
| 15 | **`status` 显示 `http://NAS地址:8765`，但该端口并未监听** | Web 页实际经系统网关 unix socket 暴露，提示沿用旧端口写法 | `resolve_paths()` 探测 socket 存在性，改提示为「飞牛桌面点图标（网关 /app/oesp-fanctl）」 |

### 更新日志

**1.15**
- ✨ **新增命令行工具，安装时自动建软链接**
  - `/usr/local/bin/oesp-fanctl`、`/usr/local/bin/fanlog` 装完即用，SSH 里不必再敲长路径
  - 卸载钩子同步清理这两个软链接，不留残留
- 🛡️ **配置写入全面加校验，写错不再生效**
  - Web 端：`POST /api/config` 合并「本次提交 + 现有配置」后校验，`TEMP_ON <= TEMP_OFF` 直接 400 拒绝（此前只提交单字段也能绕过）
  - 命令行：`oesp-fanctl set` 增加**备份 → 校验 → 失败回滚**，参数非整数 / 阈值冲突时自动还原原配置
  - 修掉 `sed` 通配把行尾注释削掉的问题（`TEMP_ON=38  # 注释` 不再被吞）
- 🐛 **修复重启后残留多个 `fanweb.py` 进程**
  - 守护进程与 `cmd/main` 的 start / stop 分支均先做单例清理，避免重复监听同一个 socket
- 🐛 **修复 `status` 里的 Web 地址误导**
  - 旧提示恒显示 `http://NAS地址:8765`，但该端口并未监听（Web 页实际经系统网关 unix socket 暴露）
  - 现改为「飞牛桌面点「OESP风扇助手」图标（网关 /app/oesp-fanctl）」
- 📊 **状态输出补充「运行模式」与 Web 访问方式**；状态文件记录 GPIO 芯片与引脚号，供 `fanlog` 实读兜底
- 📦 附：`tools/pack.py` 增加 `__pycache__` / `*.pyc` / 编辑器临时文件排除规则

**1.14**
- ✨ **Web「运行模式」改为点击即生效**（无需再点保存按钮）
  - 点「自动温控 / 常开 / 常关」立刻发请求写入配置，守护进程 2 秒内应用
  - 请求只提交 `FAN_MODE` 一个字段，**不会顺带改动温度阈值**，也不会覆盖你正在输入但还没保存的数值
  - 切换过程中按钮显示转圈并锁定，防止连点；失败会自动回退到原模式并提示原因
  - 切换成功后自动回读状态，风扇徽标 / 卡片上的「运行中 · 常开」同步刷新
  - 「保存」按钮文案改为「保存温度设置」，职责收窄为只保存 CPU / 硬盘温度阈值
- 📦 附：本版同时提供了 `pack.py` 一键打包脚本（重建 `app.tgz` → 回写 `checksum` → 产出 `.fpk`）

**1.13 / 1.12 / 1.11**
- ✨ **新增「运行模式」：常开 / 常关 / 自动温控**（`FAN_MODE`）
  - `on` 常开：风扇一直运行，忽略全部温度判定（含紧急保护），适合夏季高温 / 硬盘密集 / 求稳
  - `off` 常关：风扇一直停止，忽略全部温度判定（含紧急保护），适合夜间静音
  - `auto` 自动温控：默认，行为与 1.10 完全一致
  - 命令行 `oesp-fanctl mode <auto|on|off>`，或 Web 监控页新增的「风扇运行模式」分段选择器
  - 切换后 2 秒内生效；切回 `auto` 时会清零防抖计数与计时器，避免残留状态造成误动作
  - `status` / 状态文件 / Web 页面均显示当前模式；`常开` 模式下守护进程启动即刻开风扇
- 🐛 **修复：SSH 命令行执行 `set` / `mode` / `log` 报「配置文件不存在」**
  - 原因：这些命令直接执行时没有应用中心注入的 `OESP_FANCTL_CONF` 等环境变量，
    路径回落到并不存在的 `/etc/oesp-fanctl.conf`
  - 修复：新增 `resolve_paths()`，在环境变量缺失时按实际安装位置自动发现
    `conf` / `state` / `log` / `history` 路径（环境变量存在时不做任何改动）
- 🐛 **修复：紧急保护退出瞬间可能闪停一个周期**
  - 原因：过热强制开启只置位 `emergency`，CPU 温控路径的防抖计数没攒够，
    紧急标志一退（5℃ 迟滞）`want_on` 就掉回 0
  - 修复：进入紧急保护时同步置位 CPU 温控路径

**1.10**
- 🐛 **修复：风扇未到关闭温度就提前停**（1.6 原版遗留）
  原版用 `temp=${temp_f%.*}` 截断小数做比较，`35.9℃ → 35`，使「≤ TEMP_OFF」判定在
  **35.9℃ 就成立**，实际停止点是 36.0℃ 而不是设定的 35.0℃。
  实测日志统计：466 次停止事件里有 455 次（**97.6%**）发生在 35.0℃ 以上（89 次在 35.9℃、77 次在 35.8℃…）。
  本版改为直接比较毫摄氏度原始值，阈值精确到 0.001℃；日志温度与判定温度取自同一次读数，
  不再出现「CPU 35.2℃ <= 35℃，风扇停止」这类自相矛盾的行。
- 说明：用户自写的 `fanctl.sh` 用的是 `[ "$raw" -le 35000 ]`（毫摄氏度），本来就是精确的，
  所以体感上「原脚本 35.0℃ 才停，装了 FPK 后 35.9℃ 就停」——本版已对齐。

**1.9**
- 发布者改为「老汪不讲武德」，版本号规范化

**1.8**
- 修复 `status` 风扇状态在独立进程下恒显示"关闭"
- 修复「服务状态」误导提示（应用由应用中心托管，无独立 service）
- 修复状态文件多行解析失败

**1.7**
- 控制通路由 hwmon `fan1_target` 改为 GPIO 直控（卸载 `gpio_fan` + `gpioset`）
- 引脚从设备树动态读取，兼容 DTB 46 → 45 变更
- 安装时接管 `fanctl` / `pwm-fancontrol` / `fan-fullspeed`
- 新增 `restore` 子命令

---

## 仓库结构

```
.
├── manifest                  # FPK 清单（应用名、版本、发布者、checksum）
├── LICENSE                   # MIT
├── ICON.PNG / ICON_256.PNG   # 应用图标
├── app/                      # 应用源码
│   ├── oesp-fanctl           # 温控守护进程（bash）
│   ├── fanweb.py             # Web 监控页（Python 标准库）
│   ├── oesp-fanctl.conf.default
│   ├── config/               # 运行权限 / 资源限制
│   └── ui/                   # 应用中心 UI 配置
├── cmd/                      # 生命周期钩子（安装/卸载/配置/升级）
├── config/                   # FPK 级权限与资源声明
├── wizard/                   # 安装向导
├── tools/
│   ├── fanlog                # 实时温度监控命令
│   └── pack.py               # 一键打包脚本（重建 app.tgz → 回写 checksum → 产出 .fpk）
├── docs/
│   ├── images/               # 推文配图（封面 / 能力总览 / 模式 / 监控页 / 命令行）
│   ├── 推文-微信公众号.md
│   └── 推文-飞牛论坛.md
└── release/
    └── oesp-fanctl_1.15_arm.fpk
```

---

## 自行构建 FPK

FPK 本质是 **gzip 压缩的 tar 包**，内层 `app.tgz` 为应用文件，`manifest` 记录元数据。

**推荐：用仓库自带的打包脚本**（自动重建 `app.tgz`、回写 `checksum`、产出 `.fpk`）：

```bash
python tools/pack.py          # 用 manifest 里的版本号
python tools/pack.py 1.15     # 指定版本（同时写回 manifest）
```

**手动方式**（等价，需自行注意执行权限位与 LF 换行）：

```bash
# 1) 打包内层 app.tgz（注意保留执行权限位）
tar -czf app.tgz -C app oesp-fanctl fanweb.py oesp-fanctl.conf.default target ui config

# 2) 计算 md5 写入 manifest 的 checksum 字段
MD5=$(md5sum app.tgz | cut -d' ' -f1)
sed -i "s/^checksum.*/checksum              = $MD5/" manifest

# 3) 打包 FPK
tar -czf oesp-fanctl_1.15_arm.fpk app.tgz LICENSE cmd config ICON.PNG ICON_256.PNG manifest wizard
```

**要点**：
- `checksum = md5(app.tgz)`，不一致会被应用中心拒绝（错误码 `18000`）
- 所有脚本必须是 **LF 换行**，否则 shebang 失效
- 执行权限位必须在 tar 里保留（`chmod +x` 后再打包）
- 已安装应用无法用 `install-fpk` 覆盖（对已装应用是空操作）→ 升级流程为「应用中心停应用 → 原地覆盖文件 → 启动应用」

---

## 常见问题

**Q：装完之后风扇还是不动？**

先跑 `oesp-fanctl status` 确认：后端是否为 `gpio`、风扇状态是否为「开启」。若都正常但风扇物理上不转，请检查风扇插针是否松动。

**Q：能不能调风扇转速？**

不能。该风扇是 2pin GPIO 二态开关风扇，硬件上只有「通 / 断」两种状态。

**Q：为什么默认阈值是 38/35？**

偏保守设置（风扇基本常开）。想要安静建议改成 `45/38` 或 `60/55`。

**Q：怎么用命令行改阈值？**

```bash
oesp-fanctl set on 40     # CPU 开启温度改成 40℃
oesp-fanctl set off 36    # CPU 关闭温度改成 36℃
oesp-fanctl mode auto     # 切回自动温控
```

参数非法或「开启温度不高于关闭温度」时会被拒绝并自动回滚，原配置不受影响。

**Q：`tail -f /var/log/fan/fanctl.log` 没输出了？**

该日志属于旧的 `fanctl.sh` 脚本，已被本应用接管停用。请改用 `fanlog`（见 [使用](#使用)）。

**Q：应用中心里版本号显示不对？**

fnOS 的 `appcenter-cli` 不支持升级已安装应用（`install-fpk` 对已装应用是空操作）。版本号仅显示层面，以实际运行文件为准。升级请走「应用中心 → 卸载（保留数据）→ 重新安装」。

**Q：怎么升级到新版本？**

应用中心卸载（选择保留数据）→ 安装新 FPK。

---

## 反馈与交流

- **项目主页 / 问题反馈**：<https://github.com/wangbujiangwude/oesp-fanctl>
- **微信公众号**（应用介绍与更新）：<https://mp.weixin.qq.com/s/84uib8c63WJFSbnMQF7JOg>

---

## 许可

[MIT License](LICENSE) © 2026 老汪不讲武德
