# SWU-TIC UI/UX 精准核查与定点修复

日期：2026-08-14
基线：`a66a876`（`agent/ui-ux-third-pass-finalization`）
范围：只复核用户给出的 12 项高可信线索、`my_forms` 兼容候选和同根因局部扫描；不改 Logo、主色、Sidebar、Dashboard 或全局字体体系。

## 证据与裁决规则

- 静态证据只证明 DOM、selector、route、事件绑定和 API contract；像素与响应式结果必须由本轮浏览器实测裁决。
- 每项修改前回答 Q1（是否真实存在）、Q2（根因）、Q3（修复层级），并把修改放在 DOM、共享组件、CSS、JS state 或 route 中最合适的位置。
- 历史文档和历史截图只用于定位，不作为本轮验收证据。
- 浏览器使用隔离数据库、隔离上传目录和合成表单；未触碰生产数据，未执行解散、删除或最终提交等破坏性动作。

## Problem 1：移动端记录卡片 secondary text 错列

**状态：`CONFIRMED → FIXED`**

- Q1 / 代码与页面证据：`_records_panel.html` 的课程、教师等 `<td>` 含 `<strong>` 与 `<small>` 多个直接子项；移动 CSS 又把 `<td>` 设为两列 Grid，并用 `::before` 生成 label。本轮 390px 实测中，课程节次的 `x=30`，确实进入 label 列；value 主体从约 `x=150` 开始。
- Q2 / 根因：`::before`、`<strong>`、`<small>` 都成为 Grid item，第三项被自动排到下一行第一列。不是 margin 或文字宽度问题。
- Q3 / 修复位置：DOM 结构 + 共享移动表格 CSS；不做 `<small>`、`nth-child` 或 transform 补丁。
- 实施：在所有响应式 value cell 内加入 `.record-cell__value`，使 Grid 只布局 `::before` 与一个完整 value wrapper；wrapper 固定在第二列并允许长文本换行。
- 验证：390px 与 430px 中课程节次、教师学院均与主 value 同列；390px 课程 cell 的 wrapper 与 secondary text 均从约 `x=145` 开始，页面横向溢出为 0。
- 文件：`app/templates/user/_records_panel.html`、`app/static/css/style.css`。

## Problem 2：审核队列初始状态浪费主工作区

**状态：`CONFIRMED → FIXED`**

- Q1 / 代码与页面证据：初始 `.review-layout` 固定左右分栏并显示空 Preview。1280px 修复前队列区约 338px、Preview 约 577px，当前唯一任务“建立审核队列”被压窄，右侧为空。
- Q2 / 根因：布局没有区分 State A（未查询）、B（有队列未选中）、C（已选中）和 D（无结果），把 Preview 当成永久区域。
- Q3 / 修复位置：共享 review CSS + 现有 `loadForms()` 状态，不引入新状态机。
- 实施：默认单栏且隐藏 Preview；只有 `formGroups.length > 0` 时切换 `.is-queue-ready`。大屏 B/C 状态恢复 master-detail；`≤1024px` 保持单栏，避免窄 preview。D 状态回到全宽空结果。
- 验证：1440/1280/1024 初始筛选器全宽、Preview 隐藏；1280 有队列后分栏，选中后 Preview 正常；未来日期无结果时移除 `.is-queue-ready`；1024 有队列时仍为单栏且 Preview 隐藏。各状态横向溢出均为 0。
- 文件：`app/templates/admin/review_forms.html`、`app/static/css/style.css`。

## Problem 3：审核筛选 summary 在窄容器争抢宽度

**状态：`CONFIRMED → FIXED`**

- Q1 / 页面证据：修复前标题与描述由 `space-between` 分布；当 setup 落入约 300–350px 列时，两段文字同时换行并争抢折叠箭头空间。
- Q2 / 根因：响应逻辑跟随 viewport，而真正决定排版的是 setup 容器宽度。
- Q3 / 修复位置：summary DOM 分组 + review 容器查询。
- 实施：加入 `.review-setup__summary-copy`，为 `.review-setup` 建立 container，窄容器下让标题与描述纵向排列。
- 验证：1024/900/768 初始全宽时 summary 保持一行；390/430 变为可读的两行组合，折叠箭头不受挤压，横向溢出为 0。
- 文件：`app/templates/admin/review_forms.html`、`app/static/css/style.css`。

## Problem 4：移动端统计子导航没有横向滚动提示

**状态：`CONFIRMED → FIXED`**

- Q1 / 页面证据：390px 的 subnav 确实 overflow，滚动条又被隐藏；“部门月度考评”初始只露出前缀，进入该页时 active tab 也可能在可视区外。
- Q2 / 根因：组件只有可滚动能力，没有边缘 affordance，也没有加载后的 active-item 定位。
- Q3 / 修复位置：统计公共 partial、共享 CSS 和 app-shell JS。
- 实施：加入 `.statistics-subnav-frame` 与左右边缘提示；因全站设计契约明确禁止 gradient，最终采用轻量实体边线 + 阴影，不使用渐变。JS 在 animation frame、load、字体就绪和短延迟阶段定位 active tab，并在 scroll/resize 时同步左右边缘状态。
- 验证：390 与 430 的 active item 分别滚动约 68.5px / 37px 后完整可见；1440、1280、1024、900、768、430、390 七档全部 `activeVisible=true` 且页面横向溢出为 0。分批回归曾捕获 gradient 契约失败，改为边线 + 阴影后 RED→GREEN。
- 文件：`app/templates/partials/_statistics_nav.html`、`app/static/css/style.css`、`app/static/js/app-shell.js`。

## Problem 5：统计页移动操作过度全宽且 utility 分组错误

**状态：`CONFIRMED → FIXED`**

- Q1 / 页面证据：审表考评页在 390px 将查询、导出、全选、清空都变成约 281px 的独占行按钮；全选/清空实际只影响人员或部门范围，却混在查询 action 区。
- Q2 / 根因：共享移动规则把 filter action 全部设为 `width:100%`，DOM 又把 scope utility 放错了语义区域。
- Q3 / 修复位置：三个统计模板的 DOM 分组 + 共享移动 action CSS。
- 实施：查询/导出（以及规则保存/加载）采用紧凑两列；全选/清空移动到 `.scope-selection-toolbar`，紧邻范围搜索和选择器。
- 验证：390px 查询/导出各约 137px，430px 各约 157px；桌面仍保持约 90–97px 的自然宽度。scope utility 不再出现在 `.statistics-filter-actions`。
- 文件：`review_assessment_stats.html`、`submission_count_stats.html`、`department_monthly_assessment_stats.html`、`style.css`。

## Problem 6：考评导入模板下载与手动导入分离

**状态：`CONFIRMED → FIXED`**

- Q1 / 页面证据：模板下载位于 Page Header，而选文件、上传预览位于“手动导入考评”卡片；下载主要服务该流程，不是页面级高频全局动作。
- Q2 / 根因：同一 workflow 的前置资料与执行控件分属两个层级。
- Q3 / 修复位置：`review_assessment_stats.html` 的 action ownership，不改 route。
- 实施：从 Page Header 移除下载入口，将原 `url_for('admin.download_manual_assessment_template')` 链接移入手动导入卡片。
- 验证：390px 中 Page Header 不再含下载；“手动导入考评”标题约 `y=607`，下载按钮约 `y=667`，处于同一 section；选择文件与上传逻辑未变。

## Problem 7：审核详情内部重复通用标题

**状态：`CONFIRMED → FIXED`**

- Q1 / 页面证据：Shell 已显示“表单审核”，内容区仍只显示同名 H1，用户需继续阅读字段才能知道审核对象。
- Q2 / 根因：页面标题未绑定已经返回的 `course_title`。
- Q3 / 修复位置：`review_form.html` 的标题占位与现有加载成功分支。
- 实施：H1 改为 `#reviewObjectTitle`；加载后显示 `审核《课程名》听课表`，缺少课程名时回退 `审核表单 #ID`。
- 验证：合成表单在 390px 显示 `审核《UI Convergence Synthetic Course With A Very Long Name》听课表`，长标题正常换行且横向溢出为 0。

## Problem 8：审核动作区视觉终点顺序不合理

**状态：`CONFIRMED → FIXED`**

- Q1 / 代码与页面证据：原 DOM 顺序为提交、驳回、重置；右对齐桌面中重置成为视觉终点，移动纵排中重置成为最后一步。
- Q2 / 根因：DOM action hierarchy 与 secondary → destructive → primary 的任务终点相反。
- Q3 / 修复位置：只调整按钮 DOM 顺序；不改 handler、name/value、确认逻辑或 backend。
- 实施：顺序改为 `重置表单 → 驳回 → 提交审核`。
- 验证：390px 实际 DOM 与可点击按钮顺序一致；`resetForm()`、`rejectReview(event)`、`submitReview()` 和原确认流程均由现有回归测试覆盖。

## Problem 9：Workspace 第三个 Metric 在移动两列网格残留左分隔线

**状态：`CONFIRMED → FIXED`**

- Q1 / 页面证据：390px 第三项已换到新行第一列，但仍有 `border-left:1px` 与约 22px 左 padding。
- Q2 / 根因：桌面 `.workspace-metric + .workspace-metric` 按 DOM sibling 加分隔线；移动端变为固定两列后，DOM 邻接关系不等于网格列位置。
- Q3 / 修复位置：现有 `@media (max-width:768px)` 的固定两列规则。该断点明确为两列，因此按 odd item 清理第一列分隔是当前 grid contract，而不是临时视觉 hack。
- 实施：移动端 odd item 清除 left border/padding，第三项及以后加入 top border。
- 验证：390/430 第三项 `border-left=0`、`padding-left=0`、`border-top=1px`；桌面三列未变。
- 文件：`app/static/css/style.css`。

## Problem 10：小组管理仍是 Legacy UI Island

**状态：`CONFIRMED → FIXED`**

- Q1 / 页面与代码证据：旧页面保留独立 `<h2>`、绿色/黄色 action、inline max-height、旧 card 和不完整 modal 语义；从“人员与部门”进入时设计语言明显跳变。
- Q2 / 根因：该页面没有复用当前 Page Header、group card、empty state 与 destructive-action contract。
- Q3 / 修复位置：完整模板迁移到现有设计系统；保留 route、DOM IDs、fetch URL、字段、成员分配和密码确认。
- 实施：采用 canonical `.page-header`、`.group-card`、`.group-member-list`、`.activity-empty`；添加为 primary、编辑为 secondary、解散为 danger；补齐 modal `aria-labelledby` 与 `autocomplete="current-password"`，移除 inline max-height 和旧语义颜色。
- 验证：390px 与 1280px 均无横向溢出；实际小组卡信息完整，添加/编辑/解散层级一致；同 route fragment 刷新、写操作 URL 和密码确认测试全部通过。
- 文件：`app/templates/admin/manage_groups.html`、`app/static/css/style.css`。

## Problem 11：权限反馈未完成全仓高可信迁移

**状态：`CONFIRMED → FIXED`**

- Q1 / 代码证据：管理后台、自动审核 route 和审核模板仍有“无权访问”“您不具有审表权限”“需要审表_中心权限”等 context-free 或暴露内部 permission key 的提示。
- Q2 / 根因：已有 `permission_feedback.py` 只在部分入口使用，旧 route 各自拼 message；前端确认框直接展示内部 permission enum。
- Q3 / 修复位置：复用已有 `build_forbidden_message` / `flash_forbidden`；只替换 message 文案并给前端增加产品化 `permission_label`。不新建 helper，不改授权判定。
- 实施：高可信 HTML 拒绝使用 `flash_forbidden(resource, reason, action)`；JSON 保持原键与状态码，只把 message 改为资源/动作感知文案；自动审核保留原 `success/code/message` 和小写 `forbidden`；审核详情保留内部 enum 供逻辑使用，仅 UI 显示 `permission_label`。
- 安全：文案不插入无权访问的表单 ID、用户名称、跨部门 metadata 或原始状态。明确的“仅超级管理员可导出/定义标签”前端业务规则提示保留，因为其资源和动作已经清楚，且不是内部 permission key。
- 验证：逐一比对 74 个拒绝分支，修改前后 HTTP status、return 前缀和 JSON key 集合 `0` 不匹配；新增 5 项迁移测试先有 4 项失败、修复后 5/5 通过；相关自动审核/route/template 回归通过。
- 文件：`app/blueprints/admin.py`、`app/review_automation/permissions.py`、`app/review_automation/routes.py`、`review_form.html`、`review_forms.html`、`tests/test_permission_feedback_migration.py`。

## Problem 12：业务模板内 broad CSS selector 漂移

**状态：`CONFIRMED → FIXED`**

- Q1 / 代码证据：`course_feedback_management.html` 与 `manage_departments.html` 的 template-local `<style>` 含裸 `.table th`、`.badge`、`.card-header h6`、`.card-body .small`、`.user-list` 等，会影响页面内 modal 和共享组件。
- Q2 / 根因：局部业务样式缺少页面 ownership boundary。
- Q3 / 修复位置：模板 `body_class` + 原有 local CSS scope；不把业务专属规则继续堆入全局 CSS。
- 实施：分别增加 `course-feedback-page`、`people-management-page`，把 broad selector 前缀限定在对应页面。
- 验证：新增 contract 扫描确认两个 `<style>` 块不再包含目标裸 selector；60 个 Jinja 模板全部编译。

## Compatibility candidate：旧 `my_forms.html`

**状态：`ALREADY RESOLVED`，未修改。**

- `/user/my_forms` 是兼容 redirect；保留原 query 并强制进入 `/user/listening_registration?...&tab=records`。
- 本轮以 `user001` 访问 `/user/my_forms?search=UI`，最终进入 activity/records 渲染链，records panel 可见、旧模板标记不存在、横向溢出为 0。
- 结论：旧 `my_forms.html` 已退出主流程；为“搜索结果整洁”迁移它没有当前用户价值。

## 同根因局部扫描

本轮额外看到三个候选：返回审核队列时 preview 选择恢复、`riskFilter` URL 持久化、`submission_count_stats` 内重复的 `buildQuery/loadStats` 声明。它们分别属于状态持久化或维护性问题，并没有在本轮 viewport/状态中形成已证实的同根因 UI 缺陷，因此不扩张修改范围，记录为后续候选。

## RED → GREEN 记录

- 新增 `tests/test_ui_targeted_audit_contracts.py`；records value ownership、review state、container summary、统计导航、scope action、导入 workflow、审核标题/顺序、metric separator、group migration 和 CSS scope 在实现前出现预期失败，实现后 10/10 通过。
- 新增 `tests/test_permission_feedback_migration.py`；初次 5 项中 4 项失败、1 项已满足，迁移后 5/5 通过。
- 全量分批回归捕获 `test_design_tokens_are_charcoal_and_brick` 对 `linear-gradient` 的真实失败；删除 gradient，改为边线 + 阴影后该测试与统计导航 contract 同时通过。

## 验证总账

- 聚焦回归：137 tests，全部通过。
- 全仓 Python：单次 `unittest discover` 在 304 秒边界内未完成，未返回失败；随后对同一组 58 个测试模块使用隔离运行目录分批完成，`92 + 124 + 67 + 116 + 33 = 432` tests，全部通过。
- Jinja：60 templates 全部编译。
- JavaScript：5 files 全部通过 `node --check`。
- Python AST：`admin.py`、automation permissions/routes 共 3 files 解析通过。
- 权限 contract：74 个变更拒绝分支，status / return prefix / JSON keys 零不匹配。
- `git diff --check`：通过；仅显示 Windows 工作树 LF→CRLF 提示，无空白错误。
- 未 commit、未 push。

## 浏览器矩阵

| Viewport | Review 初始 | Statistics active tab | Group page | 页面横向溢出 |
| --- | --- | --- | --- | --- |
| 1440×800 | 单栏 setup，Preview 隐藏 | 可见 | 正常 | 0 |
| 1280×800 | 单栏 setup；有队列后分栏 | 可见 | 正常 | 0 |
| 1024×800 | 单栏；有队列时 Preview 隐藏 | 可见 | 正常 | 0 |
| 900×800 | 单栏 | 可见 | 正常 | 0 |
| 768×800 | 单栏 | 可见 | 正常 | 0 |
| 430×844 | summary 纵排 | 自动滚动后可见 | 正常 | 0 |
| 390×844 | summary 纵排 | 自动滚动后可见 | 正常 | 0 |

补充覆盖：Activity/Records、Review Queue A/B/C/D、Review Form、Review Assessment Statistics、Department Statistics、Workspace、Group Management、People/Departments。People/Departments 本轮只做相邻回归和 scope 检查，没有重新设计。

## 当前运行截图

- 修复前 records 错列：`docs/ui/screenshots/2026-08-14-targeted-audit/before/04-records-secondary-390x844.png`
- 修复后 records：`docs/ui/screenshots/2026-08-14-targeted-audit/after/12-records-390x844.png`
- 修复前 review 初始空 Preview：`docs/ui/screenshots/2026-08-14-targeted-audit/before/06-review-initial-1280x800.png`
- 修复后 review 初始全宽 setup：`docs/ui/screenshots/2026-08-14-targeted-audit/after/16-review-initial-1280x800.png`
- 修复后 review 已选中：`docs/ui/screenshots/2026-08-14-targeted-audit/after/05-review-selected-1280x800.png`
- 修复后审表考评统计：`docs/ui/screenshots/2026-08-14-targeted-audit/after/17-review-assessment-390x844.png`
- 修复后部门月度考评 active tab：`docs/ui/screenshots/2026-08-14-targeted-audit/after/18-department-stats-390x844.png`
- 修复后小组管理：`docs/ui/screenshots/2026-08-14-targeted-audit/after/19-manage-groups-390x844.png`
- 审核对象标题：`docs/ui/screenshots/2026-08-14-targeted-audit/after/01-review-form-top-390x844.png`
- Workspace metric：`docs/ui/screenshots/2026-08-14-targeted-audit/after/11-workspace-390x844.png`

## Final Verdict

**`TARGETED UI FIXES COMPLETE`**

12 项线索中 12 项经当前代码与页面证据确认后完成定点修复；`my_forms` 候选经 route/browser 证明已通过兼容 redirect 退出主流程，因此裁决为 `ALREADY RESOLVED` 并保持不改。所有修复均限定在对应根因，核心 route、权限判定、JSON shape、状态码、表单 handler、字段和 destructive confirmation contract 均保留。
