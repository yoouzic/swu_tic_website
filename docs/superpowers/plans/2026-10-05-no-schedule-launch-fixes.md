# 无课表上线和审计问题修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 无当前课表时隐藏不能工作的入口，导入并启用当前课表后恢复；修复2026-10-05演练记录的19类问题。

**Architecture:** 统一读取管理员当前学期和权威快照产生可用性状态，前端隐藏与后端守卫使用同一状态。保留手填、现场证据、历史数据和人工审核；提交与审批使用事务认领，考核规则显式绑定学期，管理员输入严格验证。

**Tech Stack:** Flask/Jinja/Bootstrap、SQLAlchemy/SQLite、pytest/unittest、JavaScript node tests、Playwright。

**Working scope:** 在当前最新工作树实施，原有未提交改动已经备份到 `output/2026-10-05-launch-fixes/source-snapshot.zip`。不提交、不推送、不改生产数据。验证只在隔离SQLite和存储目录执行。

## Task 1 当前课表可用性和可逆隐藏

**Files:** 新增 `app/services/schedule_availability.py`；修改 `app/app.py`、`app/ui/navigation.py`、`app/ui/workspace.py`、`app/blueprints/user/reservations.py`、`course_lookup.py`、`site_capture.py`、相关用户模板和JS；新增 `tests/test_schedule_availability.py`。

- [x] 编写失败测试：空当前学期、当前学期无快照、只有旧快照时不展示候选/查课/新增登记；手工提交、照片和历史入口仍在。
- [x] 编写恢复测试：为后台当前学期启用合法快照后，上述功能重新出现；旧学期不会满足当前状态。
- [x] 实现服务契约，供模板和路由共同调用：

```python
def current_schedule_availability():
    selection = resolve_current_schedule_snapshot(include_rows=False)
    return {
        'ready': selection.status == READY,
        'semester': selection.semester,
        'status': selection.status,
        'message': '' if selection.status == READY else '本学期课表未导入',
    }
```

- [x] 无课表不渲染候选控件、不加载依赖其DOM的JS；导航保留“填写听课表”和历史记录，隐藏查课及新登记；后台保留课表导入。照片保留教室确认和手填，隐藏课程查找和候选确认。
- [x] 关闭旧Course-only回退用于新增登记；旧登记不能给新表单提供当前课表核验标签；隐藏历史未使用登记选择不删除历史记录。
- [x] 运行新测试并确认RED到GREEN，复测当前课程/助手/照片相关既有测试，记录边界和恢复方法。

## Task 2 提交与审表保护

**Files:** `app/blueprints/user/forms.py`、`app/blueprints/admin/review.py`、`app/services/lecture_form_validation.py`、`app/services/review_concurrency.py`；新增提交事务服务及 `tests/test_launch_submission_safety.py`。

- [x] 使用演练E1至E9的请求写失败测试：非法ID/他人链、初次必填校验、并发提交与审核、旧登记/旧助手、兼容审核非法编辑、并发重复、旧页面覆盖、异常驳回JSON。
- [x] 初次提交验证全部必填字段、真实日期、1至14递增节次、50字反馈及两名见证人手机；历史审表保留未变旧值兼容，仅新输入执行严格校验。
- [x] `unique_id`必须为正整数、指向当前用户的真实逻辑组、处于待审核或已驳回；拒绝终审后直接重开，与现有UI一致。错误响应保留输入，不返回内部异常。
- [x] 对待审核编辑使用compare-and-set认领，与审批共享最新物理ID和更新时间条件；页面及请求带期望版本信息，检测旧页面覆盖。
- [x] 对全新提交在事务中使用原子认领，120秒内相同签名只能产生一个逻辑表单；重试拿到首个成功结果。认领的持久化状态必须在独立数据库内可复现。
- [x] 最终助手提交显式使用后台当前学期，拒绝backup和旧semester；既有本人登记的绑定事实保留，只有当前权威课程可形成无需人工审核标签，不能用旧Course为当前表单作证。
- [x] 兼容审表写入口调用与主入口同一字段校验；保留其已冻结版本行为。驳回body必须dict、reason必须string，异常返回通用可控消息且回滚。
- [x] 运行新RED/GREEN及已有审表/绑定/事务回归；重放部长/小组长到中心流程。

## Task 3 管理员输入和导入安全

**Files:** `app/blueprints/admin/contacts.py`、`system.py`、`users.py`、`org.py`、`forms_io.py`；新增 `tests/test_launch_admin_safety.py`。

- [x] 按AD-01、04、06、07、09、10写失败测试，保存RED日志。
- [x] 通讯录预览与执行按相同学号身份规则匹配，编号冲突明确拒绝；更新通讯录保留管理员角色和密码，不能消除最后一个超管。
- [x] JSON body严格对象，布尔值必须bool；角色限制为已实现枚举；姓名非空，小组容量严格正整数。不要将异常输入静默改写。
- [x] Excel人员编号按字符串读取，保留0001，新增模板回放对照测试。
- [x] 运行定向及已有设置/组织/导入事务检查，保存GREEN和持久化结果。

## Task 4 考核学期和明细

**Files:** `app/models.py`、`app/services/assessment_calc.py`、`app/utils/leave_management.py`、`app/blueprints/admin/assessment_stats.py`及数据库初始化；新增 `tests/test_launch_assessment_safety.py`。

- [x] 按AD-02、03、05、08、09写失败测试，覆盖历史春季请假切秋季、统计/明细时间一致、16周默认月和JSON类型。
- [x] AssessmentOverride增加nullable semester；新规则绑定后台当前学期。已有未绑定规则不得默认为所有新学期有效，提供保留历史及显式归属的迁移说明。
- [x] 所有月考核和请假补交消费者使用学期范围；不能只修一条查询。初始化以additive方式补列，不删除旧行。
- [x] 明细使用每条记录自己的筛选时间，与统计范围一致。周次必须整数且1至总教学周；默认月生成至当前总教学周而非固定20周。
- [x] 运行RED/GREEN及既有教学日历、请假、考核回归。

## Task 5 集成、审阅和可交付证据

- [x] 检查四块diff只覆盖授权范围，独立审阅规格符合性和代码质量，修复真实发现。
- [x] 在独立环境运行必要Python回归和全部JS测试；保留真正的失败，不把测试替身当外部服务验证。
- [x] 浏览器验证无课表时入口隐藏但手填可提交、照片可手填、首审/中心审核成功、超管设置正常；导入合成当前快照后验证入口恢复；桌面和手机截图。
- [x] 核对原始业务库和下载文件SHA-256没有改变；更新修复报告，逐项列19类问题的状态、测试和尚未验证的部署环节。
