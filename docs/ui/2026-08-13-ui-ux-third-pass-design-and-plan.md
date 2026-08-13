# SWU TIC 第三轮 UI/UX 精确收口设计与实施计划

日期：2026-08-13

基线分支：`youzi`
基线 HEAD：`45556217ce20377fcc6edff848a17071512500ce`

## 1. 目标与边界

本轮只收口当前运行态仍然成立的高价值问题：Course 的响应式内容模型与首屏筛选密度、Review 的移动操作/信息密度与审核资格呈现、Statistics 的指标密度/空趋势/当前位置语义。完成三块核心任务后，再处理证据明确且改动局部的 People 渐进披露与触达模板的 Bootstrap/可访问性债务。

保持 Flask、Jinja、Bootstrap 5、Bootstrap Icons、原生 JavaScript、现有路由、权限、查询参数、数据字段与人审流程。不重做 Workspace Shell、Sidebar、Topbar、Lecture Form、Activity Center、Review backend 流程或设计系统；不引入依赖，不调用外部 LLM，不对非隔离数据执行审核、删除、导入或批量操作。

## 2. 已核实问题

| 编号 | 状态 | 当前证据与决策 |
| --- | --- | --- |
| T3-H01 | CONFIRMED | 390/768/900 仍呈现动态桌面表格；390 下表头高 166px，内容不可扫描。新增 `<1200px` Course Summary List，桌面表格保留。 |
| T3-H02 | CONFIRMED | 390 下筛选区把表格顶部推到约 896px。保留一个主搜索行，高级筛选原位折叠，不复制 input state。 |
| T3-H03 | SUPERSEDED | 第二轮已把批量禁听移入 1+ selected 才出现的 contextual toolbar。本轮只做跨桌面/摘要列表状态回归。 |
| T3-H04 | CONFIRMED | 390 下单条审核项高约 423px，常驻查看/定义/审核/删除四个同级按钮。改为一个权威主动作 + 一个 Bootstrap More menu。 |
| T3-H05 | CONFIRMED | “审核队列已更新”成功状态长期占整行。加载成功后由列表标题数量承担持续状态，保留 aria-live announcement 但隐藏空 region；warning/error 继续常驻。 |
| T3-H06 | CONFIRMED | 1440 为 4+1，390 为五张 122px 全宽卡。增加 Statistics 专属 5 / 3+2 / 2+2+1 layout，不改共享 Metric。 |
| T3-H07 | CONFIRMED | route 当前始终传空趋势数组，模板仍绘制坐标轴。服务端显式提供 `NO_DATA` / `INSUFFICIENT_DATA` / `DATA_READY`，模板分支渲染。 |
| T3-H08 | CONFIRMED | subnav 缺“综合统计”和 `aria-current`。补当前项，不改变三个专项 endpoint 与权限。 |
| T3-H09 | CONFIRMED | 空部门已折叠，但每个 header 仍常驻查看/编辑/解散。仅空部门将编辑/解散移入带部门名的 More menu。 |
| T3-H10 | CONFIRMED | PageHeader More 与底部两张永久通讯录工具卡形成重复 IA。复杂流程原样放入默认折叠的“通讯录工具”区，并保留旧 hash 深链。 |
| T3-H11 | CONFIRMED / FIXED | Bootstrap 5.1.3 下触达模板仍使用 `badge-warning/success/danger/light`。已只迁移 Statistics 与 Review 的触达位置，未做全站机械替换。 |
| T3-H12 | CONFIRMED / FIXED | Statistics 的 `.form-group` 在当前 CSS/Bootstrap 中无定义，存在真实间距风险；已迁移为 Bootstrap 5 `mb-3`/`form-label`/`form-select`。 |
| T3-H13 | CONFIRMED / FIXED | Review 普通审核动作与 Statistics 导出仍使用 success 绿色，People 通讯录普通工具使用 warning。已改为 primary/quiet 层级；状态与风险动作仍保留语义色。 |
| T3-H14 | CONFIRMED / FIXED | 信息员登录后成功 flash 与 Workspace greeting 同屏重复。已移除成功登录 flash，失败/权限/退出反馈保留。 |
| T3-H15 | CAPTURE_ARTIFACT / VERIFIED | Workspace、System Settings、Activity Center、Lecture Form 在正常 390/1440 viewport 均无横向溢出；已补齐正常截图，不为历史异常截图改代码。 |

## 3. 方案比较与选择

### 3.1 Course

方案 A 是继续压缩/隐藏 table 列，改动小但不能解决中文表头竖排和实体扫描。方案 B 是直接套用 View Forms 的 `td[data-label]`，可复用 CSS 思路，但 Course 的“组 + 多条教学安排 + record 级选择”会丢层级。方案 C 是同一 API 数据驱动的桌面表格与 compact summary 双呈现。

采用方案 C：`>=1200px` 继续使用现有 dense dynamic table；`<1200px` 显示 Course Summary List。固定首要字段为课程名、选课编号、开课学院；每个教学安排显示教师、星期/节次、地点。展开后展示课程号、学期、学分/学时、性质及当前列设置中剩余真实字段。两种呈现共用 `currentCoursesData`、`availableColumns`、`visibleColumns`、`selectedCourses`、分页和现有 ban/register handler，不增加 API 或第二套 selection model。

筛选使用同一组 input。主搜索始终可见；学期/星期/时间段与 reset 放入 `<1200px` 默认关闭的 advanced panel，桌面默认展开。按钮维护 `aria-expanded` / `aria-controls`，筛选摘要只复述当前 DOM 值。当前实现不把筛选写入 URL，因此本轮不虚构 URL persistence；API 的 `search/semester/weekday/time_slot/page/per_page` 保持不变。

### 3.2 Review

方案 A 仅用 CSS 缩小四个按钮，仍保留错误层级。方案 B 固定“审核”为 primary，但当前前端审核资格规则与服务端 `can_review_status()` 已漂移，会呈现必失败动作。方案 C 由队列 API 返回每个版本的权威 `can_review`，前端据此选择 primary。

采用方案 C。每个版本 payload 新增只读 `can_review`，值直接来自已有 `can_review_status(session['user_id'], form.status)`；不改变人审字段或状态机。可审核项常驻“审核”，不可审核项常驻“查看”。More menu 保留查看、证据、超级管理员定义、版本历史与中心权限删除；删除继续走现有确认。移动摘要常驻“谁、课程/教师、听课时间、status、risk、coverage、版本数”，提交/更新时间在移动端隐藏、桌面保留。

成功加载时先通过 `role=status aria-live=polite` 宣告，再清空并隐藏 feedback region；列表标题的数字持续表达结果。错误、warning 与需要行动的信息不自动消失。

### 3.3 Statistics

共享 `metric()` macro 不变。Statistics 容器增加专属 class：wide 五列、medium 三列、mobile 两列且最后一项跨两列；移动卡片降低本页 padding。Subnav 补“综合统计”链接并设置 `aria-current="page"`。

趋势状态由后端根据 labels/data 的有效点数计算：0 点 `NO_DATA`，1 点 `INSUFFICIENT_DATA`，至少 2 点 `DATA_READY`。前两种显示可理解的静态状态，只有 `DATA_READY` 创建 Chart；不伪造数据，不仅用 CSS 隐藏 canvas。

### 3.4 People 与兼容性

People 不改变 Department/Group/Member 业务模型。空部门 header 保留 collapse toggle；管理动作进入带唯一 accessible name 的 Bootstrap dropdown，解散仍调用现有 password confirmation。通讯录导入/导出完整 DOM 和 handler 不动，只包入默认折叠的工具区；访问 `#contacts-import` / `#contacts-export` 时自动打开以保护 PageHeader 与 Settings 深链。

兼容性修复只限本轮触达代码及静态审计确认的真实风险。不会为清空 `form-group`、`shadow` 或颜色 utility 扫描制造无关 diff。

## 4. 文件职责

- `app/blueprints/admin.py`：Review 最新版本 + 权限的权威审核资格；Statistics 共享受权筛选、聚合、详情分页、导出与 visualization state。
- `app/templates/admin/course_feedback_management.html`：Course primary/advanced filter structure、compact summary renderer、共享 selection/expand state。
- `app/templates/admin/review_forms.html`：移动摘要层级、primary/More action renderer、紧凑成功反馈调用。
- `app/templates/admin/statistics.html`：完整 subnav、Statistics 专属 metric container、empty visualization branch。
- `app/templates/admin/manage_departments.html`：空部门 More menu、通讯录工具 details、hash reveal。
- `app/static/css/style.css`：上述页面级 responsive layout；不改变共享 component 基础行为。
- `app/static/js/review-queue.js`：feedback clear helper 与 Review menu focus restore（若 Bootstrap 默认行为不足）。
- `tests/test_ui_third_pass_contracts.py`：本轮结构/语义/响应式 contract。
- `tests/test_ui_third_pass_routes.py`：Review API `can_review`、Statistics 筛选/权限/详情/导出行为回归。
- `docs/ui/2026-08-13-ui-ux-third-pass-finalization.md`：最终基线、假设、实现、浏览器/自动化证据与停止判断。

## 5. 执行计划

### Phase T3-0 — Baseline

- [x] 记录 clean branch/HEAD/diff。
- [x] 修改前全量：363 tests / OK；JS syntax 5/5 OK。
- [x] 复制 ignored debug DB 到 `debug-ui3-baseline`，加入 6 个仅用于视觉 QA 的 `UI3-BL-*` 课程。
- [x] 启动 `127.0.0.1:5091` 隔离服务，获得 Course/Review/Statistics/People normal viewport baseline。

### Phase T3-1 — Course RED → GREEN

- [x] 在 `tests/test_ui_third_pass_contracts.py` 写 mobile summary、desktop table、single filter state、advanced panel、selection toolbar contract；6 项因结构缺失真实 RED。
- [x] 实现 summary/filter 结构；修正双 DOM checkbox 同步但保持 `selectedCourses` 单一状态，并收口桌面与详情输出转义。
- [x] 运行 Course focused tests；浏览器检查 390/768/900/960/1024/1199/1200/1440，搜索、选择、展开、字段完整性与无 body overflow。

### Phase T3-2 — Review RED → GREEN

- [x] 增加 API `can_review` 行为测试与移动 action hierarchy contract；确认 RED。
- [x] 后端使用缓存 permission + `latest_form.id` 共同计算 `can_review`；模板只根据 payload 渲染 primary；secondary/destructive 放 More。
- [x] 将移动 meta 压缩为安全扫描信息；成功加载 announcement 保持可访问但不长期占高。
- [x] 运行 Review/permission/draft/automation regressions；浏览器检查 390/768/900/960/1024/1440、Escape、焦点恢复与 overlay 焦点。

### Phase T3-3 — Statistics RED → GREEN

- [x] 写 5/3+2/2+2+1 container、subnav active、visualization state contract；确认 RED。
- [x] 实现后端状态与模板分支；保留五个 metric 和专项权限；修通原有查询、详情、分页与导出断链。
- [x] 运行 Statistics route/Bootstrap/design tests；浏览器检查 390/768/900/1024/1440、空/不足/可绘图三态及详情。

### Phase T3-4 — 收益复评与局部 Polish

- [x] 在前三块 GREEN 后复评 People/legacy；实施空部门 More 与通讯录工具 details。
- [x] 运行 People/static contracts，浏览器检查 dropdown keyboard/Escape/focus 与 hash 深链；未执行破坏性 CRUD。
- [x] 归档 H11-H15 扫描；只修触达位置的真实视觉/交互风险。

### Phase T3-5 — 最终验证与审查

- [x] AST/Jinja parse、外部 JS `node --check`、`git diff --check`、legacy 分类扫描。
- [x] 最终工作树全量 `unittest discover`：376/376 OK，420.206s，exit 0；仅既有 SQLAlchemy LegacyAPIWarning。
- [x] 完成最终 browser matrix 和 `after/` normal viewport screenshots，逐页抽检并记录 DOM 几何。
- [x] 完成 spec compliance 与 code quality 双审；主会话逐项验证、修复并要求复核。
- [x] 更新 finalization 文档并输出停止判断。

## 6. 验收底线

- Course compact 下不显示被压缩的动态 table；课程实体在 390 第一屏可见；desktop table 与列设置保留；selection/batch ban 共用原状态。
- Review mobile 不再有四个同级大按钮；不显示服务端必拒绝的“审核”；secondary/destructive 功能完整、可键盘操作、焦点可恢复。
- Statistics 390 第一屏能看到全部或大部分关键指标；1440 无孤儿卡；空趋势不绘制无意义坐标；综合统计有当前位置。
- 所有改动保持权限、人审字段、查询、分页、导出、CRUD、autosave 与 `return_to` 契约；无新依赖、无无关全站重构。
