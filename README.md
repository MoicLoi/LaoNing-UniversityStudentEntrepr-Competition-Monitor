# 辽宁省创赛平台赛事变更监听（cxcy-upln-monitor）

监听 [辽宁省大学生创新创业管理共享平台](https://cxcy.upln.cn) 赛事页（`/match`）的内容变化：
新赛事上线、截止日期调整、附件更新、报名数变化、奖项公示、证书发布。

**纯标准库、无需登录、无需浏览器**。页面是 Vue 单页应用，直接轮询它背后那组无鉴权的 JSON 接口做差量比对，
比"抓 HTML 再 diff"稳定得多（`/match` 直接请求本身是 404，见 [docs/API.md](docs/API.md)）。

- 语言 / 依赖：Python 3.8+，零第三方依赖
- 输出：JSONL 事件流 + Markdown 变更报告 + 便于二次加工的 JSON 快照
- 实测：221 个赛事基线，全量复抓零误报

## 功能特性

- **首轮建基线，不误报**：第一次运行只记录快照，之后才开始报变更
- **分层的抓取策略**：列表每轮都抓（每年度 4 个请求）；详情正文按 `detail_sweep_hours`（默认 2 小时）全量刷新，
  列表字段一变就即时抓该赛事详情
- **能区分变化类型**：`new` / `removed` / `field` / `stats` / `status` / `files` / `detail`
- **赛事消失会二次确认**：重新请求详情，区分"仅从列表下架"和"数据也被删除"
- **临时参数稳定化**：下载地址与封面里的 `?ticket=` 每次请求都变，已从比对中剔除，不会刷假变更
- **正文改动带行级 diff**：`data/changes.md` 里直接看到 `+/-` 片段
- **抗故障**：列表返回空时拒绝覆盖快照；进程锁防止定时任务与常驻进程互相踩；网络错误自动重试

## 快速开始

```bash
git clone <your-repo-url> cxcy-upln-monitor
cd cxcy-upln-monitor

cp config.example.json config.json     # Windows: copy config.example.json config.json

python monitor.py --once      # 第一轮：建立基线
python monitor.py --once      # 之后：有变化就输出
python monitor.py --loop      # 常驻，间隔见 config.json
python monitor.py --status    # 当前跟踪了多少个赛事
```

首次运行会抓取上下年度（默认去年/今年/明年）全部赛事的详情，约 1 分钟、约 220 次请求建立基线。

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--once` | 只跑一轮（默认行为） |
| `--loop` | 循环监听 |
| `--status` | 打印快照统计后退出 |
| `--interval N` | 覆盖循环间隔（分钟） |
| `--years 2026,2025` | 覆盖监听年度 |
| `--full-detail` | 本轮强制抓取全部详情正文 |
| `--quiet` | 不在控制台打印每条事件 |

## 输出文件

| 路径 | 内容 |
| --- | --- |
| `data/state.json` | 当前快照（每个赛事的全部监听字段） |
| `data/details/<id>.txt` | 详情正文纯文本，用于做正文 diff |
| `data/events.jsonl` | 追加式事件流，每行一个 JSON |
| `data/changes.md` | 给人看的变更报告，正文改动带 diff 代码块 |

`data/` 已在 `.gitignore` 中——它包含抓取到的平台内容，不建议入库。

示例事件：

```json
{"type": "stats", "field": "itemCount", "label": "报名项目数", "old": 5105, "new": 5106,
 "note": "报名项目数：5105 → 5106", "id": "178860d2...", "name": "第一届辽宁省大学生外语综合技能创新挑战赛",
 "ts": "2026-11-09 10:48:38"}
```

## 配置

把 `config.example.json` 复制成 `config.json`（本地配置，不入库）后按需修改：

```json
{
  "years": "auto",
  "year_offsets": [-1, 0, 1],
  "tabs": [1, 2, 3, 4],
  "interval_minutes": 10,
  "detail_sweep_hours": 2,
  "detail_workers": 4,
  "request_delay_sec": 0.35,
  "timeout_sec": 30,
  "retries": 3,
  "notify_command": ""
}
```

| 字段 | 说明 |
| --- | --- |
| `years` | `"auto"` 用 `year_offsets` 推算；也可写 `[2026, 2025]` |
| `year_offsets` | 相对今年的年度偏移 |
| `tabs` | 抓哪些标签页：`1` 全部、`2` 进行中、`3` 未开始、`4` 已结束 |
| `detail_sweep_hours` | 多久全量重抓一次详情正文（想更实时盯报名数就调小） |
| `notify_command` | 可选，事件发生时的外部命令，支持 `{title}` `{text}` 占位符 |

## 定时运行

方案 A：任务计划程序（Windows，每 10 分钟一轮）

```powershell
schtasks /Create /TN "辽宁创赛平台监听" /SC MINUTE /MO 10 /TR "python \"D:\path\to\monitor.py\" --once" /F
schtasks /Run    /TN "辽宁创赛平台监听"
schtasks /Delete /TN "辽宁创赛平台监听" /F
```

方案 B：cron（Linux/macOS）

```cron
*/10 * * * * cd /path/to/cxcy-upln-monitor && /usr/bin/python3 monitor.py --once >> data/cron.log 2>&1
```

方案 C：直接双击 `start_loop.cmd`（Windows）或 `python monitor.py --loop`。

## 目录结构

```
.
├── monitor.py              # 单文件监听器（全部逻辑）
├── config.example.json     # 配置模板
├── run_once.cmd            # Windows：跑一轮
├── start_loop.cmd          # Windows：常驻
├── docs/
│   └── API.md              # 接口笔记：地址、参数、字段、踩坑
├── tests/
│   └── test_monitor.py     # 离线单元测试（不联网）
├── .github/workflows/ci.yml
├── LICENSE
└── README.md
```

## 测试

```bash
python -m unittest discover -s tests -v
```

19 个用例，全部离线，覆盖 HTML 转文本、`ticket` 稳定化、字段/统计/附件/正文差量、diff 截断、年度解析。
CI（GitHub Actions）在 Python 3.9 / 3.12 / 3.13 上跑同样的用例。

## 监听范围

| 事件类型 | 含义 |
| --- | --- |
| `new` | 新赛事出现在列表 |
| `removed` | 赛事从列表消失（附带"详情页是否仍可访问"的判定） |
| `field` | 名称、起止时间、主办/协办/承办、简介、封面等字段变化 |
| `stats` | `itemCount`（报名项目数）、`memberNum`（参与人数）变化 |
| `status` | 奖项公示、证书发布、结束标记、标签页归属变化 |
| `files` | 附件新增 / 移除 / 同名替换 |
| `detail` | 方案正文改动，带行级 diff |

## 实测记录（2026-11-09）

1. 基线：2025 年 131 个赛事 + 2026 年 90 个赛事 = 221 个，详情全部抓到。
2. 03:58 一轮中"辽宁省大学生智慧海洋科技创新大赛"从 4 个标签页同时消失（2026 年总数 90 → 89），
   详情接口仍可访问，被记为"已从列表下架，但详情页仍可访问"。
3. 10:46 它又回到列表，且 `endTime` 从 `2026-11-10` 变成 `2026-11-11`——**一次真实的截止日期延长**
   （先下架改配置、再上架）。只盯网页看基本发现不了。
4. 同一批轮次还抓到：报名数 4704 → 5105 → 5106、参与人数 5474 → 5871 → 5872；
   "辽宁省大学生计算机设计竞赛"奖项公示状态 0 → 1（公示期 2026-11-09 ~ 11-12）；
   "辽宁省大学生文化创意设计大赛"证书发布状态 0 → 1。
5. 全量复抓 221 个详情后只报出 6 条真实报名数据变化，**零误报**。

## 边界与注意事项

- **只覆盖公开只读接口**。`/match/myCompetition`、`/match/studentCompetitionItem` 等需要登录（未登录 401），
  本工具不碰，也不做任何写操作。
- 请克制轮询频率。默认 0.35s 间隔、4 并发，单轮列表 4 个请求、详情 200+ 个请求，属正常访客量级。
- 列表为空（接口改版等）时脚本会**拒绝覆盖快照**并报错，不会把一次故障当成"所有赛事消失"。
- 若平台改为强制登录或加签名，列表接口会返回 401/参数异常，`data/events.jsonl` 会停止增长——
  此时可考虑带 Cookie 的会话方案，比对逻辑可原样复用。
- 本项目与平台运营方无关，仅供个人关注赛事信息使用；请遵守目标站点的使用条款。

## License

[MIT](LICENSE)
