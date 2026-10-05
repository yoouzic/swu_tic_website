# 2026-10-05 功能提交索引

本轮在 `codex/ui-theme-verified` 上，将基线 `0fe34819adda4d6652cdb0e46059d163ac332e5c` 之后的146个改动路径按功能整理。下表为13个分组提交；本索引另形成第14个提交。原有提交历史未被改写。

这些是本地提交，尚未推送或合并；本轮没有修改远程、上游配置或另一个工作树。原有146个路径逐一核对原始字节哈希一致，只追加了本索引文档。数据库、私有课表、照片、生成报告、日志和依赖环境均未纳入提交。

| 顺序 | 功能 | 提交 | 文件数 | 验证 |
|---|---|---|---:|---|
| 1 | 运行资料排除与共享测试隔离 | `78b3243` | 4 | Python 8 项 |
| 2 | 听课助手与填写助手历史方案 | `22f72c1` | 6 | 文档范围、内容一致性及差异检查 |
| 3 | 楼栋对应、地图来源与预览 | `bbbed6c` | 11 | Python 22 项，28 个子测试；含本地无头浏览器样式检查 |
| 4 | 现场线索融合方案与回放工具 | `2403529` | 11 | Python 39 项 |
| 5 | 当前课表与课程登记身份 | `a3c198d` | 23 | Python 27 项，7 个子测试；JavaScript 8 项 |
| 6 | 人员、联系人与组织管理校验 | `8c09f68` | 6 | Python 22 项，91 个子测试 |
| 7 | 按学期考核规则与月度统计 | `77c2b38` | 13 | Python 19 项，25 个子测试；JavaScript 2 项 |
| 8 | 审核版本、并发和草稿保护 | `612d147` | 15 | Python 156 项，18 个子测试；JavaScript 19 项 |
| 9 | 自动审核的课表覆盖状态 | `6e62f94` | 10 | Python 50 项，13 个子测试 |
| 10 | 表单事务、重试回执与手填草稿策略 | `3529126` | 16 | Python 40 项，26 个子测试；JavaScript 12 项 |
| 11 | 无课表界面与现场候选限制 | `f6bb0c0` | 12 | Python 35 项；JavaScript 29 项 |
| 12 | 独立无课表调试配置与启动工具 | `bbc7fd8` | 10 | Python 30 项，2 个子测试；定向选择中另有16项未运行 |
| 13 | 跨功能业务回归与页面版本契约 | `3b92cc9` | 20 | 复用完整最终源码回归及默认配置复测，另核对全部原始路径对应的 Git blob |

## 分组和依赖

同一个文件中的多组改动通过暂存区内容拆分，工作区原文件保持原样。课程身份与可用性先于审核、表单和界面提交；审核共享校验器先于表单事务；调试工具位于完整无课表行为之后。表单事务与服务器持有的手填草稿策略相互依赖，作为同一组提交。跨多个功能的回归用例与业务验收脚本在对应功能之后集中提交。

历史助手、地图和现场融合文档是已有实现的资料补录。早期方案中的范围与决策可能已被后续需求取代，当前行为以最终代码及对应测试为准。现场回放工具需要另行提供课表或照片等输入；忽略目录内的历史输入和结果不随仓库分发。

## 验证范围

- Python 完整执行：1823项通过，1157个子测试通过；1项默认配置断言因隔离环境将服务地址设为禁用的本机地址而失败。保持API密钥为空、恢复默认地址后，该项单独复测通过。这里没有将第一轮执行描述为一次全绿。
- JavaScript完整执行：157项通过。表中的定向测试与完整执行有重叠，不累计为额外测试总量。
- 最终内容检查：302个Python文件语法、62个Jinja模板编译、13个JavaScript文件语法、PowerShell启动脚本语法均通过。146个原始路径的暂存Git blob与快照一致。
- 各功能提交导出独立目录后运行定向测试；表单、现场候选和启动工具还使用临时Git索引并行验证，随后核对正式暂存树的tree SHA一致。
- 所有应用验证使用隔离SQLite和存储路径；未执行生产部署、正式表单提交或外部AI调用。新的调试工具定向验证使用合成课表。
- 强密钥模式扫描覆盖146个原始路径，未发现命中；文档中的演示账号属于本地隔离配置。
- 差异检查保留原历史文档的末尾空行，使用 `core.whitespace=-blank-at-eof`；其余空白错误检查通过。
- 仓库原有提交钩子会启动Qoder。本轮仅对提交命令临时指定空钩子目录，原钩子文件和仓库配置保持原样。

源码备份、分组计划、独立验证目录、原始日志与机器可读清单位于被Git忽略的 `output/2026-10-05-feature-commits/`。完整回归输出为 `full-python.log`、`full-node.log`，默认配置复测为 `defaults-python-defaults.log`，分组验证为 `group*-*.log`。

## 查看提交

```powershell
git log --reverse --oneline 0fe3481..HEAD
git show --stat <提交编号>
git show <提交编号> -- <文件路径>
git status --short --branch
```

## 每组文件清单

### `78b3243` 运行资料排除与共享测试隔离

`chore: exclude local evidence and isolate shared test fixtures`

- `.gitignore`
- `tests/review_request_utils.py`
- `tests/schedule_fixture.py`
- `tests/test_workbook_service.py`

### `22f72c1` 听课助手与填写助手历史方案

`docs(assistant): preserve listening and completion design history`

- `docs/superpowers/plans/2026-09-28-universal-listening-assistant.md`
- `docs/superpowers/plans/2026-10-01-form-completion-assistant.md`
- `docs/superpowers/plans/2026-10-01-listening-assistant-simplification.md`
- `docs/superpowers/plans/2026-10-01-site-capture.md`
- `docs/superpowers/specs/2026-10-01-listening-assistant-simplification.md`
- `docs/superpowers/specs/2026-10-01-site-capture-design.md`

### `bbbed6c` 楼栋对应、地图来源与预览

`docs(campus): preserve building correspondence and mapping provenance`

- `app/data/swu_building_correspondences.json`
- `docs/CAMPUS_BUILDING_MAPPING.md`
- `docs/superpowers/plans/2026-10-02-campus-building-mapping.md`
- `docs/ui/2026-10-02-all-schedule-building-models.md`
- `docs/ui/2026-10-02-building-map-correspondence.md`
- `docs/ui/2026-10-02-building-map-models.md`
- `docs/ui/2026-10-02-building-research-alignment.md`
- `docs/ui/2026-10-02-cross-map-building-research.md`
- `tests/test_site_capture_theme_browser.py`
- `tools/campus_mapping_preview.html`
- `tools/campus_mapping_preview.py`

### `2403529` 现场线索融合方案与回放工具

`test(capture): preserve context replay tools and design evidence`

- `docs/SITE_CONTEXT_ADAPTIVE_GUIDANCE.md`
- `docs/SITE_CONTEXT_FUSION.md`
- `docs/SITE_CONTEXT_REFINEMENT.md`
- `docs/superpowers/plans/2026-10-02-adaptive-site-recommendation.md`
- `docs/superpowers/plans/2026-10-02-site-context-fusion.md`
- `docs/superpowers/plans/2026-10-02-site-context-refinement.md`
- `docs/superpowers/specs/2026-10-02-site-context-fusion.md`
- `tests/test_site_context_replay.py`
- `tools/site_context_replay.py`
- `tools/site_context_trial_server.py`
- `tools/site_guidance_replay.py`

### `a3c198d` 当前课表与课程登记身份

`fix(registration): pin course identities to current timetable authority`

- `app/app.py`
- `app/blueprints/admin/courses.py`
- `app/blueprints/admin/system.py`
- `app/blueprints/admin/users.py`
- `app/blueprints/user/course_lookup.py`
- `app/blueprints/user/forms.py`
- `app/blueprints/user/listening_assistant.py`
- `app/blueprints/user/reservations.py`
- `app/models.py`
- `app/services/current_courses.py`
- `app/services/registration_course_identity.py`
- `app/services/schedule_availability.py`
- `app/static/js/activity-center.js`
- `app/templates/admin/course_feedback_management.html`
- `app/templates/user/activity_center.html`
- `app/templates/user/course_lookup.html`
- `app/ui/navigation.py`
- `app/ui/workspace.py`
- `app/utils/course_registration_limits.py`
- `tests/business_center_settings.test.cjs`
- `tests/test_course_clear_identity_reuse.py`
- `tests/test_real_schedule_handover.py`
- `tests/test_registration_course_identity.py`

### `8c09f68` 人员、联系人与组织管理校验

`fix(admin): validate personnel and organization mutations`

- `app/blueprints/admin/contacts.py`
- `app/blueprints/admin/forms_io.py`
- `app/blueprints/admin/org.py`
- `app/blueprints/admin/system.py`
- `app/blueprints/admin/users.py`
- `tests/test_launch_admin_safety.py`

### `77c2b38` 按学期考核规则与月度统计

`fix(assessment): scope overrides by semester and align monthly statistics`

- `app/app.py`
- `app/blueprints/admin/assessment_stats.py`
- `app/blueprints/admin/leave.py`
- `app/models.py`
- `app/services/assessment_calc.py`
- `app/services/assessment_override_schema.py`
- `app/services/assessment_override_scope.py`
- `app/templates/admin/_settings_assessment.html`
- `app/templates/admin/department_monthly_assessment_stats.html`
- `app/utils/leave_management.py`
- `tests/launch_assessment_settings.test.cjs`
- `tests/test_app_factory.py`
- `tests/test_launch_assessment_safety.py`

### `612d147` 审核版本、并发和草稿保护

`fix(review): reject stale decisions and preserve validated drafts`

- `app/blueprints/admin/review.py`
- `app/services/lecture_form_validation.py`
- `app/services/review_application.py`
- `app/services/review_concurrency.py`
- `app/services/review_form_queries.py`
- `app/templates/admin/review_form.html`
- `tests/review_form_state.test.cjs`
- `tests/test_business_review_fixes.py`
- `tests/test_form_version_semantics.py`
- `tests/test_review_domain_scope.py`
- `tests/test_review_form_draft.py`
- `tests/test_review_mutation_compatibility.py`
- `tests/test_review_mutation_correctness.py`
- `tests/test_review_score_validation.py`
- `tests/test_security_authorization.py`

### `6e62f94` 自动审核的课表覆盖状态

`fix(auto-review): report unavailable current timetable coverage`

- `app/blueprints/admin/review.py`
- `app/services/review_schedule_source.py`
- `app/templates/admin/review_form.html`
- `app/utils/auto_review.py`
- `tests/test_canonical_cutover.py`
- `tests/test_launch_auto_review_coverage.py`
- `tests/test_launch_review_input.py`
- `tests/test_legacy_review_compat.py`
- `tests/test_reference_data_route.py`
- `tests/test_schedule_source_fail_closed.py`

### `3529126` 表单事务、重试回执与手填草稿策略

`fix(forms): protect submission transactions and manual draft policy`

- `app/app.py`
- `app/blueprints/admin/review.py`
- `app/blueprints/admin/users.py`
- `app/blueprints/user/forms.py`
- `app/models.py`
- `app/services/lecture_form_concurrency.py`
- `app/services/lecture_form_draft_concurrency.py`
- `app/services/lecture_form_draft_entry_schema.py`
- `app/services/submission_draft_consumption.py`
- `app/services/submission_receipts.py`
- `app/templates/user/lecture_form.html`
- `tests/lecture_form_draft_csrf.test.cjs`
- `tests/lecture_form_identity_restore.test.cjs`
- `tests/test_launch_submission_lifecycle.py`
- `tests/test_launch_submission_safety.py`
- `tests/test_lecture_form_draft_csrf.py`

### `f6bb0c0` 无课表界面与现场候选限制

`fix(schedule): hide unavailable lookup and capture candidate actions`

- `app/blueprints/user/site_capture.py`
- `app/static/js/site-capture.js`
- `app/templates/user/_records_panel.html`
- `app/templates/user/_site_capture.html`
- `docs/superpowers/plans/2026-10-03-photo-guided-audit.md`
- `tests/site_capture_lifecycle.test.cjs`
- `tests/site_capture_ui.test.cjs`
- `tests/test_manual_draft_policy_safety.py`
- `tests/test_manual_draft_schedule_cutover.py`
- `tests/test_manual_only_without_schedule.py`
- `tests/test_registration_history_compatibility.py`
- `tests/test_schedule_availability.py`

### `bbc7fd8` 独立无课表调试配置与启动工具

`feat(debug): add persistent isolated no-schedule launch profile`

- `docs/LOCAL_DEBUG_LISTENING_ASSISTANT.md`
- `tests/test_local_debug_launcher.py`
- `tests/test_no_schedule_debug.py`
- `tools/debug_schedule.py`
- `tools/local_debug.ps1`
- `tools/local_debug_server.py`
- `tools/prepare_local_debug.py`
- `tools/prepare_no_schedule_debug.py`
- `无课表测试.cmd`
- `无课表测试说明.md`

### `3b92cc9` 跨功能业务回归与页面版本契约

`test(business): reconcile cross-feature acceptance and revision contracts`

- `docs/superpowers/plans/2026-10-02-business-audit-fixes.md`
- `docs/superpowers/plans/2026-10-05-no-schedule-launch-fixes.md`
- `tests/test_activity_center.py`
- `tests/test_business_acceptance_human_flow.py`
- `tests/test_business_center_fixes.py`
- `tests/test_business_departure_fixes.py`
- `tests/test_business_latest_semantics.py`
- `tests/test_business_listener_fixes.py`
- `tests/test_business_workspace_fixes.py`
- `tests/test_course_lookup_permissions.py`
- `tests/test_functional_security_fixes.py`
- `tests/test_lecture_site_capture.py`
- `tests/test_listening_assistant_evidence.py`
- `tests/test_listening_assistant_routes.py`
- `tests/test_navigation.py`
- `tests/test_photo_guided_visibility.py`
- `tests/test_registration_binding_consistency.py`
- `tests/test_submission_route_permissions.py`
- `tests/test_submit_form_transaction.py`
- `tools/business_acceptance/human_flow.py`
