# SWU TIC 第三轮 UI/UX 最终化记录

日期：2026-08-13

分支：`youzi`

审计基线：`45556217ce20377fcc6edff848a17071512500ce`
原则：隔离调试数据、无外部服务、无生产写入、无 commit/push。

## A. FINAL DECISION

`THIRD_PASS_UI_FINALIZATION_COMPLETE`

三个最后的主要布局短板已经完成从真实运行态审计、设计决策、RED → GREEN、浏览器矩阵到独立复核的闭环。本轮没有修改人审状态字段、既有 CRUD 写路径、Workspace 导航模型或 Lecture Form/Activity Center 的业务行为。

## B. VERIFIED HYPOTHESES

- `T3-H01 CONFIRMED / FIXED` — Course 在 390/768/900 仍使用桌面动态表格，中文表头被压缩；`<1200px` 已改为同数据源 compact summary，`>=1200px` 保留动态表格。
- `T3-H02 CONFIRMED / FIXED` — Course 移动端筛选把内容推到首屏以下；现为一个常驻搜索 + 默认收起高级筛选，不复制状态。
- `T3-H03 SUPERSEDED / REGRESSION-VERIFIED` — 第二轮 contextual batch toolbar 已成立；本轮验证跨呈现选择同步与 1+ selected 才出现。
- `T3-H04 CONFIRMED / FIXED` — Review 四个同级动作改为服务端权威主动作 + More；Escape 后焦点回 toggle，打开 Modal 时不抢 overlay 焦点。
- `T3-H05 CONFIRMED / FIXED` — Review 成功加载 announcement 保持 `aria-live`，但视觉高度收敛为 1px；warning/error 仍可见。
- `T3-H06 CONFIRMED / FIXED` — Statistics 五卡已实现 wide 5、medium 3+2、mobile 2+2+1；768/1024/1440 几何无孤儿留白。
- `T3-H07 CONFIRMED / FIXED` — 趋势按真实点数区分 `NO_DATA`、`INSUFFICIENT_DATA`、`DATA_READY`；无数据不再绘制空坐标轴。
- `T3-H08 CONFIRMED / FIXED` — Statistics subnav 补“综合统计”与 `aria-current="page"`，专项权限/endpoint 保留。
- `T3-H09 CONFIRMED / FIXED` — 空部门保留“查看空部门”，编辑/解散进入唯一可访问名称的 More；动作只传数值 ID。
- `T3-H10 CONFIRMED / FIXED` — 通讯录导入/导出移入默认折叠工具区，`#contacts-import`/`#contacts-export` 深链可自动展开。
- `T3-H11 CONFIRMED / FIXED` — 触达模板的 Bootstrap 4 badge token 已迁移到 Bootstrap 5；未做无关全站替换。
- `T3-H12 CONFIRMED / FIXED` — Statistics 无效 `.form-group` 已改为 Bootstrap 5 spacing/form control 结构。
- `T3-H13 CONFIRMED / FIXED` — 普通 Review 主动作使用 primary、Statistics 导出使用 quiet、People 通讯录工具不再使用 warning；状态/风险动作保留语义色。
- `T3-H14 CONFIRMED / FIXED` — 登录成功 flash 与 Workspace greeting 的重复已移除；失败、权限与登出提示保留。
- `T3-H15 CAPTURE_ARTIFACT / VERIFIED` — Workspace、System Settings、Activity Center、Lecture Form 在正常 390/1440 viewport 均无横向溢出；历史异常截图不再作为代码改动依据。

## C. MAIN IMPROVEMENTS

### Course

- 一个 API/状态模型驱动 desktop table 与 compact summary，断点在 1200px。
- 展开后包含课程号、学期、学分、总学时、课程性质与当前真实列字段；group/record 字段按正确层级回退。
- 搜索、分页、筛选参数、选择、展开、禁听 handler 保持原契约；`selectedCourses` 仍是唯一选择状态。
- DOM 只使用内部 `_domToken`，不再把导入课程键拼入 selector/inline handler。
- 桌面表格、教师/场地链接及详情内容统一输出转义；教师/场地操作改为单次事件委托和编码后的 API path。

### Review

- Queue/detail payload 的 `can_review` 同时由服务端权限、状态与“必须是最新版本”决定；前端不再重写业务规则。
- 每项常驻一个“审核”或“查看”主动作，其余操作进入 More；移动端隐藏提交/更新时间，保留听课时间、状态、风险、覆盖与版本数。
- 成功加载反馈不再长期占位；More 的 Escape 和 Modal/Offcanvas 焦点路径已真实浏览器验证。
- permission 在序列化循环外缓存，避免逐版本重复查询。

### Statistics

- Overview、明细 API 与 XLSX 导出共用 `_statistics_query()`，统一权限、日期、部门、状态语义。
- 页面筛选现在真实影响指标、聚合表、趋势、详情和导出；无效 dimension 规范化为 department。
- 修通原先不存在的详情/导出前端契约；详情用 `textContent` 构表并保留分页，导出保护 `= + - @` 公式前缀。
- 五卡响应式布局、完整 subnav 和趋势三态已完成。

### People / Compatibility

- 空部门菜单、通讯录渐进披露、唯一 accessible name、Escape 焦点恢复完成。
- Review/Statistics 触达位置完成 Bootstrap 5 badge、form spacing 与动作颜色收口。
- 信息员登录成功不再与 Workspace greeting 重复。

## D. WHAT WAS LEFT UNCHANGED

- `Workspace Shell / Sidebar / Topbar`：第二轮结构成熟，本轮 normal viewport 无 overflow，不做第三次全局重构。
- `Lecture Form`：仅补浏览器证据；表单章节、草稿/autosave 与提交契约未动。
- `Activity Center`：仅补 390/1440 normal viewport 证据；登记、预约、records 行为未动。
- `Review state machine / human review fields`：没有自动写 `status`、`reviewer_id`、`review_time`、`review_comment`；只增加只读资格序列化。
- `Course/People destructive workflows`：没有在浏览器执行禁听、解散、删除、导入或批量操作，只验证入口、确认与安全参数边界。
- `Shared Metric component`：只增加 Statistics 页面级布局 class，不修改全站 metric 基础组件。

## E. FILES CHANGED

### Phase 1 — Core UI

- `app/templates/admin/course_feedback_management.html`
- `app/templates/admin/review_forms.html`
- `app/templates/admin/statistics.html`
- `app/static/css/style.css`
- `app/static/js/review-queue.js`

### Phase 2 — Backend contracts and compatibility

- `app/blueprints/admin.py`
- `app/utils/review_permissions.py`
- `app/templates/admin/manage_departments.html`
- `app/blueprints/auth.py`

### Phase 3 — Tests and evidence

- `tests/test_ui_third_pass_contracts.py`
- `tests/test_ui_third_pass_routes.py`
- `docs/ui/2026-08-13-ui-ux-third-pass-design-and-plan.md`
- `docs/ui/2026-08-13-ui-ux-third-pass-finalization.md`
- `docs/ui/screenshots/2026-08-13-third-pass/`

## F. TEST EVIDENCE

- Baseline：`363/363 OK`，Python 3.10.11，约 407s；初始 JS syntax `5/5 OK`。
- Genuine RED：第三轮初始结构契约 `6 failures`，对应 Course/Review/Statistics/People 缺失结构，不是人为 `skip`。
- Focused：第三轮静态 + Flask route 行为测试 `13/13 OK`；审查复跑 UI/route/second-pass/Statistics/Review/People `49/49 OK`；人审状态机/draft/automation `16/16 OK`。
- Full（最终工作树）：`376/376 OK`，420.206s，exit 0。
- Python：`compileall -q app tests` OK。
- Jinja：Course、Review、Statistics、People 四个触达模板 parse OK。
- JavaScript：五个运行态内联 script + `review-queue.js`，`node --check 6/6 OK`。
- `git diff --check`：exit 0；仅 Git 的 CRLF 提示。
- 已知 warning：既有 SQLAlchemy `Query.get()` LegacyAPIWarning；本轮未扩大。

## G. BROWSER EVIDENCE

隔离环境：`127.0.0.1:5091`、复制的 debug DB/storage、fixture 账户；没有连接生产数据或外部服务。

- Course：390、768、900、960、1024、1199、1200、1440；1199 为 summary、1200 为 desktop table，全部 body overflow 0。搜索、展开字段、选择同步、contextual toolbar 通过。
- Review：390、768、900、960、1024、1440；全部 overflow 0。移动 secondary time 0 个可见、桌面 2 个可见；成功 feedback 高 1px；一个 primary + More；Escape 回 toggle；标签 Modal 保持 overlay focus。
- Statistics：390、768、900、1024、1440；全部 overflow 0。390 为 2+2+1、768/1024 为 3+2、1440 为 5；明细弹窗、筛选聚合、导出响应、趋势三态通过。
- People：390、1440；overflow 0。7 个空部门各有唯一 More accessible name；Escape 回 toggle；通讯录 hash 打开 details。
- H15：Admin/Information Officer Workspace、System Settings、Activity Center、Lecture Form 均在 390/1440 normal viewport 检查；overflow 0。信息员登录后无重复成功 flash。

截图集中于 `docs/ui/screenshots/2026-08-13-third-pass/before/` 与 `after/`。长页 full-page 图用于覆盖审计，关键 viewport 图用于视觉抽检；布局通过 DOM 几何与真实 viewport 双重确认。

## H. DOCUMENTATION

- 设计/实施计划：`docs/ui/2026-08-13-ui-ux-third-pass-design-and-plan.md`
- 最终化记录：`docs/ui/2026-08-13-ui-ux-third-pass-finalization.md`
- 截图证据：`docs/ui/screenshots/2026-08-13-third-pass/`

## I. REMAINING DEBT

### worth future work

- 将本轮手工浏览器矩阵固化为稳定的端到端 visual/keyboard smoke，重点覆盖 1199/1200、More Escape、Statistics detail pagination。
- 在独立维护任务中迁移既有 SQLAlchemy `Query.get()`，不与 UI 收口混合。

### not worth current risk

- 未触达历史模板中仍可能存在的 legacy utility/badge token；当前没有证据表明它们影响本轮主流程，不为 grep 归零扩大 diff。
- 不统一 Course/Review/People/Statistics 的页面专属密度；它们的信息模型不同，强行抽象会降低可读性。

### environment-limited

- 隔离课程 fixture 的部分中文字段因早期脚本编码显示为 `?`，这是 debug 数据生成伪影；布局、真实字段层级与交互使用英文/数值 fixture 复验，不推断生产数据内容。
- 未在隔离浏览器执行禁听、解散、删除、导入、批量审核等破坏性动作；相关权限/路由/确认由静态与 Flask tests 验证。

## J. STOP RECOMMENDATION

`UI_REFACTORING_CAN_STOP`

当前项目已经达到成熟内部工作台的主要 UI 标准：四个高频管理页在桌面、平板、手机上有清晰首要任务、合理信息密度、可解释状态、明确动作层级和无横向溢出的主流程。后续只应接受有真实用户证据或业务缺陷驱动的小型定向改动，不再进行第四轮全局 UI 重构。
