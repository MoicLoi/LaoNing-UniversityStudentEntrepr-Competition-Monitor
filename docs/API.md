# 接口笔记：cxcy.upln.cn 赛事数据

所有结论都是在 2026-11-09 直接用 HTTP 请求验证的，不是从页面推测的。

## 0. 站点形态

```
GET https://cxcy.upln.cn/          200  Vue SPA 骨架（<div id="app">，无内容）
GET https://cxcy.upln.cn/match     404  Nginx 没有 try_files 回退
GET https://cxcy.upln.cn/js/app.*.js  200  前端 bundle（API 前缀写在这里）
```

`app.js` 里：

```js
Vue.prototype.API_BASE_URL = "https://cxcy.upln.cn/provincial";
window._CONFIG["domianURL"] = API_BASE_URL;
new VueRouter({ mode: "history", base: "/" });
```

结论：**`/match` 只是浏览器里的一个前端路由**，直接请求它必然 404；页面数据全部来自 `/provincial/*` 接口。
因此监听应针对接口，而不是 HTML。

路由表（`app.js` 中的 `/match` 定义，供定位用）：

```js
{ path: "/match", name: "match", component: () => chunk("chunk-0d59db06") }
{ path: "/match/details", name: "matchDetails", component: () => chunk("chunk-549e35d2") }
```

## 1. 赛事列表

```
GET /provincial/match/competition/queryOngoing
```

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `year` | 是 | 年度，如 `2026`。省略则返回 0 条 |
| `code` | 否 | 标签页：`1` 全部、`2` 进行中、`3` 未开始、`4` 已结束 |
| `pageNo` / `pageSize` | 否 | 分页，`pageSize` 实测可到 100 |

响应结构（JEECG 风格）：

```json
{
  "success": true,
  "message": "操作成功！",
  "code": 200,
  "result": {
    "records": [
      {
        "id": "178860d2256a6b34d3f2b4b5b9a9b2bd",
        "name": "第一届辽宁省大学生外语综合技能创新挑战赛",
        "year": "2026",
        "startTime": "2026-11-08 00:00:00",
        "endTime": "2026-11-14 23:59:59",
        "createTime": "2026-04-23 10:33:08",
        "synopsis": "第一届辽宁省大学生外语综合技能创新挑战赛",
        "coverUrl": "/sys/common/static?ticket=4aaede2dd493447f8eed5c9c2f90d461"
      }
    ],
    "total": 6, "size": 50, "current": 1, "pages": 1
  },
  "timestamp": 1794196246568
}
```

注意：

- 鉴权：**不需要**。没有 Cookie、没有 token，直接 GET 即返回。
- 四个 `code` 的集合并不相等。2026 年实测：`code=1` 只有 6 条，`code=3`/`code=4` 各 90 条。
  想不漏赛事，必须把四个标签页都抓一遍再按 `id` 去重（本项目的做法）。
- `coverUrl` 里的 `ticket` 每次请求都会重新生成，**不能直接拿来做变更比对**。

## 2. 赛事详情

```
GET /provincial/match/competition/detail?id=<赛事ID>
```

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 列表返回的 `id`。用 `competitionId` 等其它名字会返回 `{"success":false,"message":"参数异常！","code":501}` |

返回字段（35 个，按监听价值排序）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `detail` | string | 赛事方案/通知正文，HTML |
| `fileList[]` | array | 附件：`name`、`size`、`type`、`url`（含临时 `ticket`） |
| `itemCount` | int | 报名项目数，实时变化 |
| `memberNum` | int | 参与人数 |
| `awardPublicStatus` | int/string | 奖项公示开关，`0`/`1` |
| `awardPublicTime` / `awardPublicEndTime` | string | 公示起止，`YYYY-MM-DD` |
| `certificatePublishStatus` | int | 证书发布开关 |
| `finishFlag` / `finishTime` | int / string | 是否结束、实际结束时间 |
| `host` / `coOrganizer` / `undertakeName` | string | 主办 / 协办 / 承办 |
| `name` / `year` / `synopsis` / `startTime` / `endTime` | - | 基本信息 |
| `coverPhoto` / `coverUrl` | string | 封面 |
| `schoolAdmin` / `provincialDirector` / `assessType` | - | 多为 null |
| `roleTypes` | int | 角色类型 |
| `itemCount` 同级的 `itemName` 等 | - | 未使用 |

## 3. 需要登录、本工具不用的接口

| 接口 | 未登录状态 |
| --- | --- |
| `/match/myCompetition` | 401 |
| `/match/studentCompetitionItem` | 401 |
| `/match/schoolCompetitionList` | 401 |
| `/match/competition/queryYears` | 401 |
| `/match/competitionItem/isJoinedCompetition` | 401 |

`queryYears` 要登录有点反直觉，但实测如此；年度列表在前端是本地生成的（`this.Date.getFullYear()+1` 往前遍历到 2015）。

## 4. 踩过的两个坑

**TLS。** 站点握手需要 legacy renegotiation，OpenSSL 3 默认禁用，会报：

```
URLError: <urlopen error [SSL: UNSAFE_LEGACY_RENEGOTIATION_DISABLED] unsafe legacy renegotiation disabled>
```

修法：

```python
ctx = ssl.create_default_context()
ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
```

**临时 `ticket`。** `/sys/common/static?ticket=<32位hex>` 每次请求都不一样（封面和附件都是）。
直接比对 URL 会让每轮都误报几十上百条"变化"。比对时要剥掉 query，附件则按 `名称+大小+类型` 比对。

## 5. 实测数据量

- 2025 年：131 个赛事。
- 2026 年：90 个赛事（其中"全部"标签只展示 6 个）。
- 建立完整基线（220 个详情）= 约 1 分钟、约 220 次请求。
- 之后的常规轮次只要 4 次列表请求（该年度 × 4 个标签页）。
