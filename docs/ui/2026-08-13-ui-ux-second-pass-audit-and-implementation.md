# SWU TIC Website 第二轮 UI/UX 深度探索、优化实施与验证

日期：2026-08-13
状态：实施、视觉验证、静态验证与最终全量回归全部完成
范围：当前 `youzi` 工作树；不更换 Flask / Jinja / Bootstrap 5 技术栈，不改变业务路由、权限和 API 契约。

## 1. Baseline

### 1.1 Git 与运行环境

- Branch：`youzi`
- 起始 HEAD：`0b2c9e4cb18b4d4a8fa8e6750abece3ec44fb6cb`
- 起始状态：clean；本轮没有 commit / push / reset。
- Python：复用 `../SWU_TIC-main/.venv/Scripts/python.exe`
- 隔离运行时：`data/instance/debug-ui2-rich/lecture_forms-debug.db`
- 隔离存储：`data/storage/debug-ui2-rich/`
- 本地服务：`http://127.0.0.1:5089`
- 浏览器：Codex 内置浏览器；使用 `super` / `user001` debug 用户；未触达生产数据。

### 1.2 修改前证据

- 修改前截图：`docs/ui/screenshots/2026-08-13-second-pass/before/`
- 修改前覆盖了管理员/信息员工作台、审核、人员、课程、统计、表单列表、导出模态框、听课与填报、长表单等页面。
- 修改前完整测试曾启动于 clean tree，但 330 秒运行期间已进入实施阶段，因此不把该次结果包装成严格隔离的“pre-change baseline”。它最终为 `342 tests / OK / 326.749s`；本轮末尾的独立完整重跑才是最终回归依据。

### 1.3 RED 证据

新增 `tests/test_ui_second_pass_contracts.py` 后，首次运行：

```text
Ran 10 tests in 0.040s
FAILED (failures=28)
exit code 1
```

失败覆盖审核队列顺序、移动页头动作层级、课程批量上下文、统计顺序、空部门折叠、工作台紧凑空态、搜索作用域、图标系统、Bootstrap 4 契约和移动表单摘要。

## 2. Current-State Reassessment

第一轮已经把系统带入统一工作台阶段：共享 shell、charcoal + brick 视觉令牌、Bootstrap 5、任务型工作台、审核双栏、移动 drawer、详情 modal / offcanvas 和局部组件契约都已成立。本轮不重做这些成果。

第二轮的真实瓶颈不是品牌风格，而是信息优先级和深层迁移债务：

- 高频任务仍可能被筛选、请假管理或大量页头动作挡住；
- 部门和空态在窄屏占用过多纵向空间；
- 全局搜索和上下文搜索在同一屏争夺注意；
- 旧 Font Awesome 类没有对应字体资源，实际图标宽度为 0；
- 深层模板仍残留 Bootstrap 4 控件/间距契约；
- 管理员表单列表在 390px 下只能靠横向滚动并把中文挤成逐字换行。

本轮保留现有字体、配色、圆角、Bootstrap Icons 和服务端契约，只调整结构、组件契约与响应式行为。

## 3. Hypothesis Verification

| ID | 状态 | 修改前证据 | 决策与结果 |
|---|---|---|---|
| H2-01 | CONFIRMED | 审核页需先“查询→选范围→加载”，加载后筛选/范围仍占主栏上方；桌面整页约 1519px。 | 把队列表单移到主栏第一位置；范围与筛选改为可折叠 setup；加载后自动收起并隐藏引导。加载后列表 top 约 283px，页面约 904px。 |
| H2-02 | CONFIRMED | 390px 人员页 6 个整行按钮，header 约 350px；课程约 304px；统计有 3 个整行跳转按钮。 | 人员/课程页使用“主动作 + 更多操作”语义分组；统计跳转改模块子导航；移动 header 分别约 178px / 178px / 86px。 |
| H2-03 | CONFIRMED | 统计指标 top 约 545px，请假管理先于核心指标。 | 指标移到 page header / subnav 后；请假管理改为默认折叠的次级模块。指标 top 约 248px（1440）/ 261px（390）。 |
| H2-04 | CONFIRMED | 人员页 9 个部门中 7 个空部门仍渲染完整卡片，桌面页约 2768px。 | 空部门默认折叠、非空部门展开、搜索命中时展开；桌面页约 1726px。高风险成员操作没有被盲目合并。 |
| H2-05 | NOT_CONFIRMED | 工作台指标已使用 border-block 的 plain metrics，computed `box-shadow: none`。 | 保持现状；不为了数量再次去卡片化。 |
| H2-06 | CONFIRMED | 信息员 390px 空态约 182px 高，快捷入口 top 约 773px，页面约 1020px。 | empty-state macro 增加 compact 变体；空态约 91px，快捷入口 top 约 608px，页面约 855px。 |
| H2-07 | CONFIRMED | 听课与填报桌面同时出现 topbar 搜索和页面课程搜索；两者目标语义重叠。 | 由当前 endpoint 决定是否显示全局搜索；已有上下文搜索的页面只保留页面搜索。 |
| H2-08 | CONFIRMED | 课程页“批量禁听”在未选择课程时仍长期驻留页头且 disabled。 | 移至 selection contextual toolbar；0 项时完全隐藏，显示选中数和清空选择。续作在隔离 DB 创建一次性课程，真实验证了桌面与 390px 的 1 项选中态、按钮启用和无页面横向溢出，随后清除数据。 |
| H2-09 | CONFIRMED | base 只加载 Bootstrap Icons；14 个模板有 147 个 Font Awesome token，浏览器 computed width 为 0。另发现 7 个当前 icon 包不存在的 `bi-*` token。 | 全模板统一为当前本地 Bootstrap Icons；同步改 JS 字符串与 chevron classList；静态校验 110 个使用中的 `bi-*` token 均存在于本地 CSS。 |
| H2-10 | CONFIRMED | 深层模板存在 62 个 Bootstrap 4 控件/方向间距命中；导出 modal 使用 `custom-control`。 | 全模板清理 `custom-control*`、`mr-*` / `ml-*`、旧 data/input-group 契约；导出 modal 迁移为 `form-check`。最终目标扫描为 0。 |

## 4. Newly Discovered Findings

### H2-11 — CONFIRMED / FIXED：管理员表单列表移动端不可读

修改前 390px 表格内部 `scrollWidth 560px / clientWidth 300px`，中文表头和数据逐字换行。实施后在 `<=768px` 使用字段标签摘要布局：9 个字段全部保留，`scrollWidth == clientWidth == 285px`，页面无横向溢出。

### H2-12 — CONFIRMED DURING QA / FIXED：新增 overflow menu 关闭后焦点回退

浏览器首次验证发现 dropdown 用 Escape 关闭后焦点落到 `body`。在共享 `app-shell.js` 增加 `hidden.bs.dropdown` 焦点回退；最终浏览器证据：ArrowDown 打开并聚焦首项，Escape 关闭后 active element 回到 `courseMoreActions`。

### H2-13 — CONFIRMED DURING FULL REGRESSION / FIXED：最小 DOM 测试桩缺少新交互依赖

第一次独立全量回归中，352/353 项通过；唯一失败来自既有移动抽屉 Node 测试桩没有实现 `document.querySelectorAll`，无法承载新增的页头 dropdown 焦点生命周期。浏览器实现本身有效，因此只给测试桩补充返回空集合的 `querySelectorAll()`，单项、focused 与第二次全量回归全部转绿。

### H2-14 — CONFIRMED IN CONTINUATION / FIXED：小组写操作成功后整页刷新

续作把两处 `location.reload()` 改为同路由 HTML 读取：检查 `response.ok`，使用 `DOMParser` 提取服务端重新渲染的 `#groupsList`，通过 `document.importNode` 只替换该列表。这样继续复用服务端权限过滤、成员数和成员列表，不在客户端复制业务模型。真实浏览器完成一次性小组新增、编辑、解散；topbar 搜索框中的 `NO-RELOAD-SENTINEL` 三次均保留，URL 不变，数据库小组数最终从基线 1 回到 1。写成功而读回失败时保留旧列表并给出 warning，而不误报写失败。

### H2-15 — CONFIRMED DURING WRITE QA / FIXED：课程“减号”不能解除已有禁听

真实批量禁听写入后发现：已有禁听只显示无可访问名称的减号，点击只从前端 `selectedUsers` 移除，确认按钮仍调用 add-only 批量接口；后端已有 DELETE 路由却从未被 UI 使用。续作记录每个用户在所选课程中的 `{courseId, banId}`，把“取消新选择”和“移除已有禁听”分成两种显式动作；后者通过共享确认 modal 调用 DELETE，并重新读取服务端状态。真实浏览器完成新增禁听、显示“已在 1 门所选课程禁听”、确认解除，数据库禁听数从 0→1→0。

### H2-16 — CONFIRMED DURING VISUAL REVIEW / FIXED：成员操作菜单被滚动容器裁剪

成员行改为“查看 + 更多”后，契约和键盘测试最初均通过，但截图显示 dropdown 被 `.user-list { overflow-y:auto }` 裁成行内滚动条。续作在成员 dropdown 打开期间临时给对应列表加 `user-list--menu-open`，关闭后恢复滚动约束。最终 1440px 与 390px 截图中菜单均完整可见；390px 菜单矩形位于 viewport 内，页面宽度 `375 <= 390`，Escape 关闭后焦点回到触发按钮。

### H2-17 — CONFIRMED DURING FINAL SCAN / FIXED：一个 Bootstrap 4 `sr-only` token

最终全模板扫描发现 `admin/review_form.html` 动态加载状态仍使用 Bootstrap 4 的 `sr-only`。增加 class-token 契约后替换为 Bootstrap 5 `visually-hidden`；未修改加载文案或审核行为。

## 5. Prioritization

| Priority | 内容 | 处理 |
|---|---|---|
| P0 | H2-01 审核首任务、H2-03 统计首信息、H2-09 无效图标、H2-11 移动表单可读性 | 本轮完成 |
| P1 | H2-02 页头动作层级、H2-04 空部门密度、H2-06 空态、H2-07 搜索作用域、H2-08 批量上下文、H2-10 BS4 债务 | 本轮完成 |
| P2 | H2-12 dropdown 焦点回退、H2-13 最小 DOM 测试桩、H2-14 小组局部刷新、H2-16 菜单裁剪、H2-17 `sr-only` | 验证阶段发现并在续作完成 |
| P3 | 已成立的 H2-05 plain workspace metrics | 不修改 |

## 6. Implementation Decisions

### 6.1 Review：队列 DOM 优先，而不是 CSS 位移

选择把 `formsListArea` 真正移到 setup 之前；加载成功后收起 `<details>`。没有用 absolute/order CSS 假装换序，也没有默认拉取所有中心权限数据，避免扩大请求量和审核范围风险。

### 6.2 PageHeader：业务分组，而不是把 6 个按钮缩小

人员页保留一个角色相关主动作，其余进入 Bootstrap dropdown；课程页保留“列设置”，导入/模板/统计进入 dropdown；批量禁听离开 header。这样桌面和移动共享同一语义，不需复制 DOM ID 或 handler。

### 6.3 Statistics：核心数据优先，管理模块延后

专项统计改成轻量 subnav；五项指标先出现；请假管理保留完整功能但默认折叠。没有删除管理员能力，也没有把请假逻辑挪到新路由。

### 6.4 People：压缩无信息区域，并建立成员动作层级

用 Bootstrap collapse 压缩 `total_users == 0` 的部门；非空部门和搜索结果仍完整保留。续作经真实桌面/移动可用性验证后，成员行只常驻“查看”，编辑、离任、删除进入带唯一可访问名称的 Bootstrap dropdown；原有权限门、确认行为和 handler 不变。菜单打开时只临时释放所属用户列表的 overflow，避免破坏长列表的日常滚动约束。

### 6.5 Search：按 endpoint 解释作用域

全局搜索仍存在于没有上下文搜索的页面；人员、课程、统计、审核、表单列表和听课登记等页面隐藏 topbar search。不是全局删除搜索。

### 6.6 Icon / Bootstrap：全仓模板契约收口

不新增 Font Awesome/CDN。所有 FA token 映射到项目已加载的 Bootstrap Icons，JS 动态字符串同步迁移；对映射后 token 再与本地 icon CSS 做存在性校验。Bootstrap 4 控件和方向间距在 `app/templates/**/*.html` 范围完整清理。

### 6.7 写操作同步：服务端片段优先于客户端重建

小组列表继续由 Jinja 生成，写 API 保持原样；局部刷新读取同一页面并只替换 `#groupsList`。课程禁听则复用已有 GET/POST/DELETE API，前端只补足“已有记录”身份和解除动作。两处都没有新增路由、扩大权限或复制服务端成员/禁听规则。

## 7. Changed Files

### Phase A — 信息架构与共享行为

- `app/app.py`
- `app/static/css/style.css`
- `app/static/js/app-shell.js`
- `app/templates/partials/_topbar.html`
- `app/templates/partials/_empty_state.html`
- `app/templates/main/workspace.html`
- `app/templates/admin/review_forms.html`
- `app/templates/admin/manage_departments.html`
- `app/templates/admin/manage_groups.html`
- `app/templates/admin/course_feedback_management.html`
- `app/templates/admin/registration_statistics.html`
- `app/templates/admin/statistics.html`
- `app/templates/admin/view_forms.html`

### Phase B — Icon 与 Bootstrap 5 全模板收口

- `app/templates/list.html`
- `app/templates/auth/login.html`
- `app/templates/user/lecture_form.html`
- `app/templates/user/my_forms.html`
- `app/templates/admin/_settings_automation.html`
- `app/templates/admin/_user_detail_panel.html`
- `app/templates/admin/auto_review_results.html`
- `app/templates/admin/dashboard.html`
- `app/templates/admin/department_monthly_assessment_stats.html`
- `app/templates/admin/form_detail_content.html`
- `app/templates/admin/import_forms_excel.html`
- `app/templates/admin/registration_statistics.html`
- `app/templates/admin/review_assessment_stats.html`
- `app/templates/admin/review_form.html`
- `app/templates/admin/submission_count_stats.html`
- `app/templates/admin/user_detail.html`
- Phase A 中包含旧 token 的模板。

### Phase C — Tests

- `tests/test_ui_second_pass_contracts.py`
- `tests/test_course_page_header_migration.py`
- `tests/test_people_page_header_migration.py`
- `tests/test_people_responsive_contract.py`
- `tests/test_statistics_design_migration.py`
- `tests/test_course_ban_management_ui.py`
- `tests/test_manage_groups_interactions.py`

### Phase D — Evidence / Documentation

- `docs/ui/2026-08-13-ui-ux-second-pass-audit-and-implementation.md`
- `docs/ui/2026-08-13-ui-ux-second-pass-continuation-plan.md`
- `docs/ui/screenshots/2026-08-13-second-pass/`

## 8. Visual Evidence

### 8.1 Before

- Review loaded：`before/03-review-queue-loaded-1440x900.png`
- People mobile：`before/09-people-390x844.png`
- Statistics mobile：`before/11-statistics-390x844.png`
- View Forms mobile：`before/13-view-forms-390x844.png`
- Information Officer Workspace：`before/14-information-officer-workspace-390x844.png`
- Activity desktop：`before/17-activity-center-1440x900.png`
- Lecture Form：`before/18-lecture-form-1440x900.png`

### 8.2 After

- `after/01-review-setup-1440x900.png`
- `after/02-review-loaded-1440x900.png`
- `after/03-review-loaded-390x844.png`
- `after/04-people-1440x900.png`
- `after/05-people-390x844.png`
- `after/06-course-1440x900.png`
- `after/07-course-390x844.png`
- `after/08-statistics-1440x900.png`
- `after/09-statistics-390x844.png`
- `after/10-view-forms-1440x900.png`
- `after/11-view-forms-export-modal-1440x900.png`
- `after/12-view-forms-390x844.png`
- `after/13-information-officer-workspace-390x844.png`
- `after/14-activity-center-1440x900.png`
- `after/15-lecture-form-390x844.png`
- `after/16-system-settings-1440x900.png`
- `after/17-system-settings-390x844.png`
- `after/18-admin-workspace-1440x900.png`
- `after/19-admin-workspace-900x800.png`
- `after/20-review-mobile-detail-390x844.png`

### 8.3 Continuation

- `continuation/01-course-selected-1440x900.png`
- `continuation/02-course-selected-390x844.png`
- `continuation/03-people-actions-1440x900.png`
- `continuation/04-people-actions-390x844.png`
- `continuation/05-groups-local-refresh-1440x900.png`

### 8.4 Browser Matrix

| Page / State | 1440×900 | 1280 | 1024 | 960 | 900×800 | 768 | 390×844 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Admin Workspace | PASS | — | — | — | PASS | — | — |
| Information Officer Workspace | — | — | — | — | — | — | PASS |
| Review Queue loaded | PASS | — | PASS contract | PASS contract | PASS shell | PASS responsive | PASS |
| Review mobile detail | — | — | — | — | — | — | PASS (`390px` dialog, `375px` body, no overflow) |
| People | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Course | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Group add/edit/disband | PASS | — | — | — | — | — | — |
| Statistics | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Lecture Form | — | — | — | — | — | — | PASS |
| System Settings | PASS | — | — | — | — | — | PASS |

五档矩阵中 People / Course / Statistics 的 `body.scrollWidth <= viewport width`；960px 使用 sticky sidebar，900px 进入 fixed drawer，768px 应用移动 header 契约。

## 9. Test Evidence

### 9.1 Focused

```text
Second-pass RED: Ran 10, FAILED (failures=28), exit 1
Second-pass GREEN: Ran 10, OK, exit 0
Focused regression group (final): Ran 66, OK, exit 0
Dropdown focus RED: Ran 1, FAILED, exit 1
Dropdown focus GREEN: Ran 1, OK, exit 0
Full-regression compatibility reproduction: Ran 1, FAILED, exit 1
Compatibility harness GREEN: Ran 1, OK, exit 0
Second-pass + app-shell focused recheck: Ran 12, OK, exit 0
Continuation people actions RED: Ran 3, FAILED (failures=1), exit 1
Continuation people actions GREEN: Ran 3, OK, exit 0
Menu overflow visual-contract RED: Ran 4, FAILED (failures=6), exit 1
Menu overflow GREEN + focused: Ran 17, OK, exit 0
Course existing-ban removal RED: Ran 3, FAILED (failures=6), exit 1
Course existing-ban removal GREEN + header: Ran 6, OK, exit 0
Group local-refresh RED (Luna Max): Ran 4, FAILED (failures=11), exit 1
Group local-refresh GREEN: Ran 4, OK, exit 0
Final focused regression: Ran 49, OK, exit 0
Final BS5 sr-only GREEN: Ran 12, OK, exit 0
```

### 9.2 Static / Syntax

```text
git diff --check: PASS, exit 0 (only Windows LF/CRLF notices)
Python AST: AST_OK 106, exit 0
JavaScript syntax: JS_STATIC_OK, exit 0
all-template scans: 0 for FA and BS4 direction spacing/components
all-template sr-only class-token scan: 0
modified high-frequency templates: 0 for browser dialogs/new windows/reload
icon token existence: 110 used tokens, 0 missing
```

### 9.3 Full Regression

```text
First independent run: Ran 353 in 405.320s, FAILED (failures=1), exit 1
Root cause: the existing minimal Node DOM harness did not expose document.querySelectorAll,
            which the new page-header dropdown focus lifecycle legitimately uses in browsers.
Minimal fix: extend the test harness with querySelectorAll() returning an empty list.
Final independent run: Ran 353 in 397.500s, OK, exit 0
Continuation final fixed-tree run: Ran 363 in 426.200s, OK, exit 0
Non-failing warnings: existing SQLAlchemy LegacyAPIWarning messages
Logs:
  - docs/ui/screenshots/2026-08-13-second-pass/final-unittest.log
  - docs/ui/screenshots/2026-08-13-second-pass/final-unittest-continuation.log
```

### 9.4 Runtime / Browser

- 隔离 debug service 启动并健康响应；模板和 Python context 修改后主动 restart。
- Review：真实 read-only query/scope/load 流程；1 个表单组；setup 自动折叠。
- People：真实 API 返回 9 部门；7 个空部门折叠；成员“更多”在 1440px / 390px 完整可见，移动端无页面横向溢出，Escape 关闭并恢复触发按钮焦点。
- Course：创建唯一一次性课程并验证 selected toolbar；真实批量禁听写入后发现并修复解除缺口，浏览器完成 0→1→0 可逆写入。
- Groups：真实新增、编辑、解散一次性小组；URL 和 topbar sentinel 保持，证明没有页面导航/刷新；数据库总数 1→2→1。
- Statistics：真实路由/Chart 渲染；指标优先；请假 details 默认 closed。
- View Forms：1 条真实 debug 表单；导出 modal 打开/关闭；11 个 BS5 form controls；移动摘要标签全部可见。
- Search：Activity 页面 topbar search 为 0，页面上下文搜索保留。
- Accessibility：Course dropdown 以 ArrowDown 打开并聚焦首项；Escape 关闭并回到 toggle；review detail fullscreen modal 无横向溢出。
- 清理：一次性 course / ban / group 标记均不存在；最终隔离计数为 courses 0、listening_bans 0、groups 1、users 3、lecture_forms 1；5089 已停止监听。

## 10. Remaining Known Debt

1. Review 首次进入仍需明确一次时间/成员范围。本轮确认 URL 已保存筛选和成员 ID；继续保留显式范围选择，避免自动扩大中心级审核查询与 reviewer intent。
2. 浏览器写入只覆盖本轮直接相关且可逆的课程禁听和小组新增/编辑/解散。没有执行审核提交、真实用户删除、导入或导出下载，因此不把这些流程声明为本轮人工验收通过。
3. 隔离 SQLite 打开 `PRAGMA foreign_keys=ON` 时暴露既有 schema debt：`lecture_bans.course_id` 引用了非唯一的 `courses.course_code`，触发 `foreign key mismatch`。当前应用默认连接未开启该 pragma，本轮未越界修改表结构；应作为独立数据迁移任务处理。
4. H2-05 为 NOT_CONFIRMED；workspace plain metrics 已符合当前设计，不列为未完成工作。
