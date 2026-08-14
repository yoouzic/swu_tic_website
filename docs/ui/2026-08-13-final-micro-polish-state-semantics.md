# SWU TIC Website — Final Micro-Polish & State Semantics

> 执行窗口：2026-08-13 至 2026-08-14（Asia/Shanghai）
> 分支：`agent/ui-ux-third-pass-finalization`
> 基线提交：`41f75dffa2b5cf7194fce16adeabd66d9a48b61b`
> 结论：`UI_FINAL_MICRO_POLISH_COMPLETE`

## Baseline

- 实际 Git checkout 为 `SWU_TIC-submit`；本轮开始时工作树干净。
- 修改前完整套件：395/395 通过，测试时长 442.982 秒，退出码 0；仅有既存 SQLAlchemy `Query.get()` `LegacyAPIWarning`。
- 浏览器使用隔离调试实例 `http://127.0.0.1:5096`，数据库位于仓库外的 `tmp/final-micro-polish-5096`，未连接业务数据库。
- 修改前在真实浏览器复现了：Workspace 固定“上午好”和零状态“处理审核”、Statistics 操作按钮孤行、Course 手机操作分占两行、Review 日期从数字中间断行、People 解散动作与编辑长期并列、Review 页面标题同级重复。
- 已成熟的 Workspace 结构、Review Queue、Member Drawer、Course 响应式内容模型、Statistics IA、Lecture Form、Activity Center、Sidebar 和 Topbar 均作为冻结边界。

## Verified Hypotheses

| Hypothesis | Result | Evidence / decision |
| --- | --- | --- |
| MP-01 Workspace 时间问候 | CONFIRMED | 管理员和超级管理员固定“上午好”；应用没有足够的业务时区契约，改为中性“你好”。 |
| MP-02 Workspace CTA 与 pending state | CONFIRMED | 0 和正数待审此前共用“处理审核”及不自然 summary；已建立 0/positive 状态文案。 |
| MP-03 Statistics filter actions | CONFIRMED | 1440、1280、1024、900、768、390 均观察到原列宽造成不自然换行或孤立按钮。 |
| MP-04 Course mobile header density | CONFIRMED | 768 和 390 下“列设置”“更多操作”各占一行并将搜索区向下推约 52px。 |
| MP-05 Review statistics range | CONFIRMED | 390 modal 中结束日期 `2026-08-13` 曾在数字中间断开。 |
| MP-06 Page title duplication | CONFIRMED | Topbar“表单审核”与页面“表单审核管理”处于重复层级；页面改为“审核队列”。 |
| MP-07 People destructive hierarchy | CONFIRMED | 部门、小组的“编辑 / 解散”长期并排，红色噪声且误触风险高。 |
| MP-08 Member Drawer information duplication | NOT_CONFIRMED | “个人信息”承担摘要，“详细信息”承担完整字段，属于合理 Summary + Details。 |
| MP-09 Statistics 请假按钮语义 | CONFIRMED, VALID | 超级管理员为当前教学周登记请假是考核豁免动作，warning 表达业务例外风险；保留。 |
| MP-10 Legacy visual islands | DEFERRED | `admin/import_users.html` 有旧装饰色，但当前路由未注册，只被旧 dashboard 引用；不满足高频可达门槛。 |
| MP-11 Flash / greeting duplication | NOT_CONFIRMED | 登录成功低价值 flash 已在既有实现中移除，并有回归契约。 |
| MP-12 同根状态文案 | CONFIRMED, FIXED IN SCOPE | Workspace 的 0/positive copy 是唯一高收益同根问题；其余高频页面未发现新的 P0/P1 状态错配。 |

## Newly Discovered Issues

- 未发现需要继续扩大范围的 P0/P1。
- Statistics 在一次“刚 resize 即读取”的 390px 测量中短暂报告 overflow；等待 Chart.js / 布局稳定 1.5 秒后重新测量为 0，截图亦无可见缺陷，判定为测量时序而非产品问题。
- 聚焦回归首次运行时，旧测试仍要求“解散部门”为外露 `btn-outline-danger`；这是与已确认新层级冲突的旧契约，更新为下拉菜单内 `text-danger` 后同批 120 项全部通过。

## Changes Implemented

### Workspace

- 管理员和超级管理员统一使用 `你好，{name}`，不引入未经定义的时区逻辑。
- `pending_forms > 0` 时保留“处理审核”，summary 明确数量和优先级。
- `pending_forms == 0` 时使用“查看审核队列”，summary 明确当前无待审并引导查看提交情况。
- 保留 Greeting、Summary、Metric strip、Priority、Quick Actions 主结构和原 endpoint。

### Statistics

- 新增局部 `.statistics-filter-actions` flex group，以 `gap` 管理查询、重置、导出。
- 操作区获得完整行分配，去除按钮各自 `ms-2`；查询仍为 Primary，重置/导出仍为 Secondary。
- 未改变筛选字段、导出 handler、图表、详细统计或模块导航。

### Course

- 只给 PageHeader actions 增加 `.course-page-actions`，在 768/390 保持“列设置 / 更多操作”同一行。
- 保留 accessible name、现有 dropdown items、触控尺寸、dense table / summary list / selection / advanced filter 模型。

### Review

- 统计范围拆分为 label 和 value，日期使用独立 `<time>` token，窄屏可换行但日期内部不拆分。
- 页面标题改为“审核队列”，与 Topbar 的应用上下文“表单审核”形成清晰层级。
- 保留 neutral cards、完成/未完成 accent、modal 关闭路径和 Review Queue 架构。

### People

- 高频部门和小组保留外露“编辑”，将“解散部门 / 解散小组”迁入清晰可见的“更多”菜单。
- 菜单项保持 `text-danger`；原 handler、ID、payload、密码确认和取消路径不变。
- 使用原生 button + Bootstrap dropdown；真实浏览器确认 Enter 展开、Escape 收起并恢复到触发按钮。

### Legacy

- 未修改不可确认高频可达性的旧 import/dashboard visual island；记录为 deferred debt，不做全仓颜色清理。

## What Was Intentionally Frozen

- Sidebar / Topbar：导航与焦点行为已成熟，本轮没有相关真实缺陷。
- Workspace 信息架构：只修状态文案，不重新 Card 化或重排首页。
- Review Queue / Statistics modal 主结构：只修标题与日期 token，不重做队列、卡片或交互流。
- Member Drawer：摘要与详细信息层级合理；仅做桌面/手机回归验证。
- Course：不改变 `>=1200` dense table、`<1200` summary、advanced filter、selection、batch toolbar、column settings。
- Statistics：不改变 IA、子导航、图表、导出/筛选业务合同。
- Lecture Form、Activity Center、System Settings：只纳入 shared CSS 回归矩阵。
- 技术栈、组件库、动画系统、数据模型、权限、路由与 API 合同均未改变。

## Static Scan Classification

扫描范围为 `app`（排除 vendored Bootstrap 内容），结果按文件数记录：

| Term | Files | Classification |
| --- | ---: | --- |
| `上午好` | 0 | FIXED |
| `处理审核` | 1 | VALID：仅 positive pending state |
| `ms-2` | 13 | FIXED in Statistics；其余为既存局部 spacing，NOT_RELEVANT |
| `btn-warning` | 8 | VALID/DEFERRED：真实 warning 或未触及低频区域 |
| `btn-success` | 10 | VALID/DEFERRED：状态与既存低频区域 |
| `btn-info` | 6 | VALID/DEFERRED：状态与既存低频区域 |
| `bg-warning` | 16 | VALID/DEFERRED：状态或 legacy island |
| `bg-success` | 19 | VALID：通过/成功状态；部分 legacy deferred |
| `bg-info` | 16 | VALID/DEFERRED：信息状态或 legacy island |
| `表单审核管理` | 0 | FIXED |
| `解散` | 3 | VALID：业务路由、确认与可发现菜单文案 |

## Test Evidence

### RED → GREEN

- 修改前核心契约批次：22 项中 7 项按预期失败，分别覆盖 Workspace 状态、Statistics group、Course mobile actions、Review date tokens 和 People destructive hierarchy。
- Review 标题契约单独 RED：1 项按预期失败。
- 最小实现后核心批次：26/26 通过，2.683 秒。

### Final gates

- Focused cross-module regression：120/120 通过，测试时长 8.515 秒（命令 wall 9.745 秒），失败 0，退出码 0。
- Full regression：401/401 通过，测试时长 393.860 秒（命令 wall 396.693 秒），失败 0，退出码 0。
- Warnings：既存 SQLAlchemy `Query.get()` `LegacyAPIWarning`；无新增失败或错误。
- Python syntax：`AST_OK 52`。
- JavaScript syntax：`JS_OK 5`。
- Jinja compile：`JINJA_OK 59`。
- `git diff --check`：退出码 0；仅 Git 的 LF→CRLF 工作树提示，无 whitespace error。
- 最终 tracked diff（报告前）：12 个文件，154 insertions / 27 deletions；范围为 6 个应用文件和对应测试。

## Browser Evidence

所有浏览器检查均在隔离数据库、超级管理员 `super` 或管理员 `manager` 会话中完成；没有操作业务数据库。

| Page | Role | Viewport | State / interaction | Result |
| --- | --- | --- | --- | --- |
| Workspace | super | 1440×900 | 0 pending | neutral greeting、自然 empty copy、“查看审核队列”、overflow 0 |
| Workspace | super | 1440×900 | 3 pending synthetic fixture | 数量 copy、“处理审核”、3 个优先任务；截图后删除 fixture，remaining 0 |
| Workspace | manager | 1440×900 | 0 department pending | neutral greeting、部门语义 copy、“查看审核队列” |
| Statistics | super | 1440/1280/1024/900/768/390 | filter actions | 三个动作在六档宽度均为一行，无孤立按钮，稳定态 overflow 0 |
| Course | super | 1440/768/390 | header actions + More | actions 一行；390/768 搜索区上移约 52px；dropdown items 保持，overflow 0 |
| Review | super | 1440/390 | open statistics modal | label/value 自然换行，两个日期 token 完整，关闭按钮可用，neutral cards 不变 |
| People | super | 1440 | department/group More | 解散项可发现且 danger；点击后密码确认弹窗出现，Cancel 无写入 |
| People | super | 1440 | keyboard | `Enter` 展开、`Escape` 收起；`aria-expanded` true→false，焦点留在 `departmentActions1` |
| Member Drawer | super | 1440/390 | open member detail | Summary + Details 层级合理；390 body overflow 0，关闭可用 |

Regression Freeze Matrix 在 1440 与 390 重新打开 Workspace、Review、People、Course、Statistics、Lecture Form、Activity Center、System Settings；稳定布局均无 body 横向溢出。Statistics 新鲜页面控制台为 0 errors / 0 warnings。

## Screenshot Paths

- `docs/ui/screenshots/2026-08-13-final-micro-polish/workspace-pending-zero-1440x900.png`
- `docs/ui/screenshots/2026-08-13-final-micro-polish/workspace-pending-positive-1440x900.png`
- `docs/ui/screenshots/2026-08-13-final-micro-polish/statistics-actions-desktop-1440x900.png`
- `docs/ui/screenshots/2026-08-13-final-micro-polish/statistics-actions-mobile-390x844.png`
- `docs/ui/screenshots/2026-08-13-final-micro-polish/course-header-mobile-390x844.png`
- `docs/ui/screenshots/2026-08-13-final-micro-polish/review-statistics-mobile-390x844.png`
- `docs/ui/screenshots/2026-08-13-final-micro-polish/people-destructive-actions-1440x900.png`

## Deferred Debt

- SQLAlchemy `Query.get()` deprecation warnings remain technical debt outside this UI pass.
- `admin/import_users.html` and old dashboard visual-island markup remain, but current route reachability is not established; migrate only if a real reachable flow is restored.
- Existing semantic status colors and scattered spacing utilities remain intentionally; grep presence alone is not a defect.
- Automated browser QA is complete, but human visual acceptance remains a separate optional product sign-off.

## Final Freeze Decision

The confirmed P1 state-semantics, micro-layout and destructive-action hierarchy issues are resolved. Focused, full, syntax, template and browser gates pass; no remaining user-visible P0/P1 was found.

`SYSTEMATIC_UI_REFACTORING_SHOULD_STOP`

Future UI changes should require a reproducible user-visible defect or a new product requirement, not a broad cleanup scan.
