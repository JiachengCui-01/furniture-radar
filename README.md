# 家具爆品雷达（卖家精灵自动选品监控）

每隔 3 天自动从**卖家精灵**拉取亚马逊美国站**板材 / 实木 / 铁木为主的家具**（柜类、桌类、床架、置物架；不含沙发、床垫、椅子）的最新数据，每期独立做一次完整分析，识别四类商品。报告生成后推送到**钉钉群**，点开卡片即可查看。

| 板块 | 含义 |
|---|---|
| 🚀 **突然爆火** | 近 1~2 周销量和排名明显跃升，且没有异常信号。细分为持续型、脉冲型、爬升型、新品起量；另外标注季节性、大促、降价驱动 |
| 🎨 **爆火产品外观与工艺特征** | 当期爆火、潜力和上升中（近 28 天增长 ≥30%）的商品，在主材质（板材 / 实木 / 铁木，优先取亚马逊商品详情的 Material 属性，并和持续热销商品对比）、风格、材质、造型、工艺、颜色、功能卖点上，比全部商品明显多见的特征。来源有两个：标题里的设计词（与约 1800 个家具商品对比），以及 AI 识别商品主图（与持续热销商品对照），附主图墙和一句话外观要点 |
| 🌱 **潜力产品** | 上架半年内、销量持续增长、评论还不多、没有异常信号 |
| 🔥 **真正持续热销** | 连续多个月保持子类目头部销量、波动小、没有下滑、没有异常信号 |
| ⚠️ **假爆火 / 异常信号** | 留评率异常、评论增速远超销量、评分短期跳升、短时脉冲、评论集中在少数几天、非验证购买多等。按分值提醒，仅供人工核实 |

所有板块和外观分析样本只收**评分 ≥ 4.0** 的商品：每个板块按排序取前几名，评分不达标的剔除，由后面的商品依次补位；评分不达标的也不占追踪名额。

报告是单个网页，手机和电脑都能看，支持深色模式。点顶部的四个数字，会以小窗弹出对应板块，页面不会跳动。报告和历史数据都经过加密：钉钉消息里的链接自带密钥，点开就能看；没有链接的人即使找到网址也只能看到乱码。

> 不想花积分先看效果？运行 `python -m radar demo`，会用模拟数据生成一份完整报告（见下文“本地预览”）。

---

## 工作原理

```
GitHub Actions：每天 08:17（北京时间）检查一次，距上次成功运行满 3 天才真正执行
  1. 发现（每个自然月只做一次，约 45 次调用）
     家具 / 办公家具两个根类目 → 自动展开子类目，只保留柜类、桌类、床架、置物架
     （剔除沙发、床垫、脚凳、各类椅子、天篷、屏风、推车、塑料折叠桌；标题含软包 / 布艺抽屉 / 塑料的商品也剔除）
     → 每个子类目取头部 50 个商品，再查一次新品 → 得到约 1500~2000 个 ASIN 的月度基线
  2. 近 30 天榜单（每期刷新，约 6 次调用）：销量增长榜、BSR 上升榜、新品榜，数据截至当天
     + 上期爆款的相似款（每个爆款 1 次调用，带出约 15 个同类商品的近 30 天数据）
  3. 追踪池（本地计算，约 145 个，同一父体只取一个，评分 < 4.0 不占名额）：
     必看（往期爆火/潜力/异常 + 每个子类目销量第一 + 全部类目里销量最大的）→ 相似款
     → 机会候选（约 300 个，按机会分排序：分数最高的 20 个每期都看，其余按“最久没查”轮流，约 5 期轮完一遍）
  4. 刷新日数据（每个 ASIN 1 次调用，返回约 400 天的日销量 / BSR / 价格 / 当前评论数）
     每期刷新全部追踪商品，报告完全反映当期最新情况
  5. 判定 → 对可疑商品额外核查评论和变体（每期最多 5 个）→ 外观与工艺特征 → 与上期对比
  6. 可选：用 DeepSeek 识别爆火/潜力商品的主图（风格、材质、造型、工艺、颜色），并把计算结果写成中文简报（只措辞，不判定，没有 key 就用模板）
  7. 生成报告 → AES-256-GCM 加密 → 发布到 GitHub Pages → 推送钉钉卡片
```

- 卖家精灵 `product_research` 传 `month` 时只返回**已结束月份**的月末快照（查当月返回的也是上月数据）；**不传 `month`** 时返回截至当天的**近 30 天**数据，`asin_competitor`（相似款）也是近 30 天数据。月度快照做稳定的基准，近 30 天榜单负责每期发现新机会，最终判定都看每个 ASIN 的**日数据**（`asin_prediction`）。
- 日销量是卖家精灵根据 BSR 估算的，所以判定以 **BSR 中位数**为主信号，销量只用作门槛和倍数。
- 卖家精灵 MCP 的接入方式与 AI工作台 完全相同：`https://mcp.sellersprite.com/mcp`，请求头 `secret-key`。客户端代码直接复制自 AI工作台。

### 第一期和之后的区别
- **第一期就能用**：突然爆火、季节性判断（日数据有约 400 天）、潜力、持续热销、留评率异常、新品评论/销量比、短时脉冲、评论核查。
- **第二期起增加**：两期之间的评论增速、评分跳升、变体合并识别、与上期对比（新进榜、标签变化、爆火回落）。

---

## 给自己或同事部署（约 15 分钟）

### 1. 复制仓库
在 GitHub 上 **Fork** 本仓库，或用 “Use this template” 新建一个。Fork 出来的仓库需要到 **Actions** 页点一下 “I understand… enable them” 才能启用定时任务。

### 2. 准备 4 样东西
| 名称 | 从哪里来 | 必填 |
|---|---|---|
| `SELLERSPRITE_SECRET_KEY` | https://open.sellersprite.com → 获取密钥 | ✅ |
| `REPORT_KEY` | 运行 `python -m radar keygen` 生成。**务必备份**，丢失后历史数据无法解密 | ✅ |
| `DINGTALK_WEBHOOK` + `DINGTALK_SECRET` | 钉钉群 → 群设置 → 机器人 → 添加机器人 → 自定义。安全设置勾选 **加签**，复制 SEC 开头的密钥和 webhook 地址。多个群用英文逗号分隔，按顺序一一对应 | 推荐 |
| `DEEPSEEK_API_KEY` | https://platform.deepseek.com ，用于生成文字总结和识别商品主图（每期约 20 张图，每张约 300 tokens） | 可选 |

### 3. 写入 GitHub Secrets
**方式 A（推荐，Windows）**：安装 [GitHub CLI](https://cli.github.com) 并执行 `gh auth login`，然后在仓库目录运行：
```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_secrets.ps1
```
脚本会逐项提示输入，也可以直接读取本地 `.env` 中已有的值。没有 `REPORT_KEY` 时会自动生成一个并写入 `.env`。

**方式 B（网页）**：仓库 → Settings → Secrets and variables → Actions → New repository secret，逐个添加上表中的名称和值。

### 4. 开启 GitHub Pages
仓库 → Settings → Pages → Build and deployment → Source 选 **GitHub Actions**。

### 5. 运行第一期
仓库 → Actions → **家具爆品雷达** → Run workflow（mode 选 `run`）。
如果只是想让最近一期报告用上新的样式或分析方法，mode 选 `rerender`：不调用卖家精灵，钉钉里原来的链接打开就是新版本。首次运行大约 5~10 分钟，完成后钉钉群会收到卡片消息。之后每 3 天自动运行。

> 注意：公开仓库的 Actions 日志任何人都能看到。程序在 CI 里只打印数量统计，不打印 ASIN、标题或链接，报告密钥也会被打码。请不要在工作流里加 `--verbose` 一类会输出商品数据的调试命令。

---

## 推送到更多钉钉群

每个群在 GitHub 上单独存一个 Secret，新增群时不用改原来的配置：

1. 在新群里添加自定义机器人，安全设置选“加签”，复制 Webhook 和 SEC 开头的密钥。
2. 仓库 → Settings → Secrets and variables → Actions → **New repository secret**：
   - Name：`DINGTALK_ROBOT_1`，第二个群用 `DINGTALK_ROBOT_2`，依此类推（工作流里预留了 1 ~ 5；还要更多就在 `.github/workflows/radar.yml` 里照样加一行）
   - Secret：`Webhook地址,SEC密钥`（中间英文逗号；机器人用“关键词”方式的话只填 Webhook）
3. 保存即可，下一期起这个群也会收到。不想再推送某个群，删掉对应的 Secret 就行。

`DINGTALK_WEBHOOK` / `DINGTALK_SECRET` 继续有效，可以和 `DINGTALK_ROBOT_*` 同时用。
注意：GitHub 的 Secret 保存后不会再显示原值，点“编辑”看到的是空的，这是正常的。
工作流里每个 Secret 都是逐个写出来的。不要改成 `toJSON(secrets)` 一次性读取全部：GitHub 会把这种写法判为可疑工作流，之后每次运行都要人工批准。

---

## 本地运行与预览（Windows）

```powershell
cd 自动化卖家精灵分析
pip install -r requirements.txt
copy .env.example .env          # 然后编辑 .env 填入密钥
```

| 命令 | 作用 |
|---|---|
| `python -m radar demo` | 用模拟数据生成演示报告，**不花积分**。输出 `out\demo\report.html` |
| `python -m radar run --force --budget 20` | 真实运行一期，最多调用 20 次（适合首次试跑） |
| `python -m radar run --force --notify` | 运行并推送钉钉 |
| `python -m radar test-dingtalk --with-key` | 发一条测试卡片，检查机器人配置以及链接中的 `#k=` 是否保留 |
| `python -m radar search-category 沙发` | 按关键词查类目 nodeIdPath |
| `python -m radar probe` | 列出卖家精灵 MCP 全部工具及参数（不计费）。加 `--tool X --args '{…}'` 可调用一次并保存原始响应 |
| `python -m radar decrypt site\reports\2026-10-08.html` | 本地解密报告（`data\state.enc` 也可以） |
| `python -m radar status` | 查看历史数据概况 |
| `python -m radar rerender` | 用已保存的数据重新生成最近一期报告，不调用卖家精灵，报告链接不变。改了报告样式或分析方法后用它刷新 |
| `python -m radar keygen` | 生成新的 `REPORT_KEY` |
| `python -m pytest` | 运行测试（不会调用卖家精灵或钉钉） |

也可以用 `scripts\run_local.ps1 -Force`。本地运行的数据保存在 `site\` 目录，与 GitHub 上 `site` 分支的数据互相独立。

---

## 调整参数：`config.yaml`

所有阈值和范围都在 `config.yaml` 里，每项都有中文注释。常改的几项：

- `scope.roots` / `scope.include_label_regex` / `scope.exclude_label_regex`：监控哪些类目（按子类目英文名匹配保留 / 剔除）
- `scope.exclude_product_regex`：标题里出现这些词的商品直接剔除（默认：软包、布艺抽屉、塑料、树脂）
- 修改监控范围后，下一期会立即重新发现子类目和头部商品，不用等到下个月
- `budget.per_run` / `budget.monthly_cap`：每期和每月的卖家精灵调用上限
- `pool.max_size` / `pool.core_max` / `pool.similar_max`：追踪多少个 ASIN，其中必看和相似款各占多少
- `pool.opportunity_*`：机会候选的门槛（销量、增长、上架时长）和每期必看的高分个数
- `discovery.risers_*` / `discovery.similar_*`：近 30 天榜单和相似款的查询量
- `schedule.min_days_between_runs`：运行间隔（默认 3 天）
- `thresholds.min_rating`：上榜的评分下限（默认 4.0）
- `thresholds.*`：各类判定阈值
- `deal_windows`：大促日期。Prime Day 等每年日期不同，公布后补充进去，避免把大促销量误判为爆火
- `llm.vision` / `llm.vision_focus` / `llm.vision_reference`：主图识别开关，以及识别多少个爆火/潜力商品和对照用的持续热销商品
- 设计词库在 `radar/detect/design.py` 的 `LEXICON` 里，可以按需补充风格、材质、工艺等词条

### 调用量估算（默认配置）
| 项目 | 调用次数 |
|---|---|
| 发现（每月 1 次，或改范围后） | 约 50 |
| 需要重新发现的那一期 | ≤230 |
| 之后每期 | ≤175（近 30 天榜单约 6 + 相似款约 3 + 追踪池约 145 个商品全部刷新 + 可疑商品核查 + 新出现商品的材质查询） |
| 每月合计 | 约 1700，上限 1800 |

如果和 AI工作台 用的是同一个卖家精灵 key，两边消耗的是同一份积分。AI工作台 的定时采集每天最多 150 次。

---

## 安全与隐私

- **加密方式**：`REPORT_KEY` 是主密钥，用 HKDF-SHA256 派生出两类子密钥：
  - 数据密钥：加密 `data/state.enc`
  - 报告密钥：每份报告一把，钉钉链接 `#k=` 后面就是它

  泄露一个链接只会暴露那一份报告，不会暴露历史数据。
- **链接中的密钥**：`#` 后面的部分不会发送到任何服务器。页面解密后会把密钥从地址栏去掉。
- **在别处打开报告**：打开任意一期报告页，粘贴报告密钥或主密钥即可查看（主密钥会在浏览器里派生出对应报告的密钥）。
- **网站首页**：只列出报告日期，不含任何商品信息。网站不包含 `data/` 目录。
- **仓库体积**：`site` 分支每次运行只保留最新的一次提交，不会越积越大。网站保留最近 60 期报告。
- **更换密钥**：生成新的 `REPORT_KEY` 后，旧报告和历史数据都无法再解密，相当于从头开始。如需保留，请先用 `python -m radar decrypt` 导出。

---

## 常见问题

**国内打开报告很慢或打不开？**
github.io 在部分网络下不稳定。钉钉卡片里已经写出了重点提醒，不打开网页也能看到结论。可以给 Pages 配置自定义域名，并在仓库 Settings → Variables 里设置 `REPORT_BASE_URL`；也可以把 `site/` 同步到阿里云 OSS 等国内静态托管。

**钉钉提示“sign not match”或“关键词不匹配”？**
确认 `DINGTALK_SECRET` 是 SEC 开头的加签密钥，且与 webhook 对应。如果机器人用的是“自定义关键词”方式，请把关键词设为“雷达”。

**报告里“数据陈旧”是什么意思？**
该 ASIN 的日数据超过 4 天没有更新，可能是卖家精灵数据延迟，或商品已下架。

**为什么某个爆品没有被追踪到？**
追踪池受预算限制。机会候选（近 30 天榜单、相似款、月度名单里增长快或上架半年内的）大约 300 个，每期只能查一部分：
分数最高的每期都查，其余约 5 期轮完一遍。不在任何候选里的商品（例如近 30 天还没进各榜单前 50）要等它上榜才会被发现。
可以调大 `discovery.risers_per_root`、`budget.per_run`，或调低 `pool.core_max` 给机会候选多留名额。报告底部“全部追踪商品”的“来源”一列写明了每个商品是怎么被选中的。

**异常信号就是刷单吗？**
不一定。它只说明数据有异常，例如变体合并、站外引流、大促、真实口碑爆发，也可能触发其中几项。报告列出了每一项依据和分值，请人工核实。

---

## 目录结构

```
radar/
  cli.py            命令行入口
  pipeline.py       一次完整运行的流程
  mcp_client.py     卖家精灵 MCP 客户端（复制自 AI工作台）
  vendor.py         调用预算、去重、returnFields、按 schema 组织参数
  discovery.py      子类目展开 + 头部/新品发现
  tracking.py       追踪池、日数据刷新、轮换与探索
  series.py         时间序列计算
  detect/           爆火 / 热销 / 潜力 / 假爆火 / 外观与工艺（design.py 设计词库）/ 与上期对比
  vision.py         商品主图外观识别（可选）
  verify.py         评论与变体核查
  narrative.py      文字简报（DeepSeek 或模板）
  report/           报告 HTML、加密外壳、迷你图
  notify/dingtalk.py 钉钉加签与卡片消息
  demo.py           模拟数据（演示和测试用）
config.yaml         所有可调参数
.github/workflows/radar.yml  定时任务
scripts/            Windows 辅助脚本
tests/              测试（含卖家精灵真实响应样本）
```
