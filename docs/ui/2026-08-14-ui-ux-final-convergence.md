# SWU-TIC 最终阶段 UI/UX 深度审计与收口报告

> 审计基线：`71fff95e06fcfcc42d96b523f900ee1cc5e643bc`
> 工作分支：`agent/ui-ux-third-pass-finalization`
> 日期：2026-08-14
> 发布边界：仅提交本轮审计范围并推送个人私有 `origin`；部门 `upstream` 保持禁用。

## 结论

本轮没有重做已经成熟的 Workspace Shell，而是沿用现有配色、字体、间距、圆角、PageHeader、Activity Panel 与权限模型，对统计、审核和“我的表单”三条高频路径做最后收口。核心问题已经从“页面各自可用”推进到“同一产品语言、同一响应式逻辑、同一反馈契约”。

完成后：

- 三个管理员统计子页共享 PageHeader 和统计子导航，筛选、结果区与主次操作使用同一套布局原语。
- 审核队列筛选从 viewport 栅格改为 container query：窄队列栏双列、1024px 全宽队列三列、手机单列。
- 审核详情由嵌套卡片改为一个 surface 内的七个语义 section，保留全部字段、DOM ID、API 与审核流程。
- 审核主操作、危险操作、次要操作恢复清楚层级；移动评分表不再压缩成不可读的四列。
- 参考数据抽屉具备语义按钮、ARIA 状态同步、Escape 关闭与焦点返回。
- 实际用户入口 `_records_panel.html` 的九列表格在手机端变为带字段标签的记录卡，不再依赖横向滚动。
- 权限拒绝反馈补齐“动作 + 资源 + 原因”，并修复经理侧栏展示超级管理员专属课程入口的问题。
- 服务端 Flash 消息关闭按钮改用产品自己的消息契约，真实点击后能够移除消息。
- Luna 扫描出的确认型 Bootstrap 4 残留已清除：管理员模板不再包含 `form-group`、`btn-xs`、`badge-warning/success/danger` 或 `thead-light`；没有把 Bootstrap 5 的 `btn-close` 当成误报。

## 已确认问题与处理

### 1. 统计页设计系统分叉

问题：三个统计子页使用各自的标题、按钮颜色、筛选卡与结果布局；成功色被误用为普通导入/导出动作；子页之间没有稳定的同级导航。

处理：新增 `_statistics_nav.html`，将综合统计、交表数量、审表考评、部门月度考评收敛为同一子导航；子页统一使用 PageHeader、`statistics-tool-grid`、`statistics-filter-grid`、`statistics-workspace-grid`。查询保留为唯一主动作，导出、全选、清空、上传预览等改为中性次要动作。搜索输入补齐显式 label，统计 modal 补齐 `aria-labelledby`。

`registration_statistics.html` 保留在“课程与登记”信息架构中，只复用 PageHeader 和动作层级；没有错误地并入管理员统计子导航。

### 2. 审核队列响应式规则与真实容器脱节

问题：原筛选区依赖 `col-md-*`，同一规则同时服务桌面分栏中的窄队列栏和 1024px 的全宽队列，产生不合理的列数与孤立末项。

处理：使用命名容器 `review-filters`。浏览器复测后又将双列阈值从 24rem 校正为 20rem：

| 状态 | 容器宽度 | 实际列数 | 页面级横向溢出 |
| --- | ---: | ---: | ---: |
| 1440px 桌面分栏 | 343px | 2 | 0 |
| 1024px 单栏队列 | 676px | 3 | 0 |
| 390px 手机 | 271px | 1 | 0 |

1280px 默认浏览器下，预览仍在右侧且队列容器仅 278px，因此筛选为单列；这是容器真实宽度驱动的预期行为，不再由 viewport 猜测。

### 3. 审核详情层级过重、动作混级

问题：外层大卡片内继续套多个 section 卡片和审核强调卡；提交、驳回、重置使用同等级大色块；模板包含大段局部 CSS。

处理：收敛为 `.review-form-surface` + 七个 `.review-form-section`，嵌套 `.card` 数量为 0；局部样式迁入集中 CSS。动作区现在是：提交审核 `btn-primary`、驳回 `btn-outline-danger`、重置 `btn-outline-secondary`。表单字段、name、ID、审核意见、草稿、评分 payload、确认弹窗和返回队列行为保持不变。

### 4. 移动评分输入不可读

问题：390px 下原四列约为“原因 104px / 部门 63px / 个人 63px / 操作 44px”，多条评分时难以读写。

处理：动态单元格补齐 `data-label`；575.98px 以下将每条评分改为带边界的纵向字段组。实测 390px 时每个字段宽 314px，四个字段均为 grid 行，页面横向溢出为 0。

### 5. 参考数据抽屉键盘契约缺失

问题：标题区域由可点击 div 充当按钮；打开后 Escape 不关闭，ARIA 状态与焦点没有闭环。

处理：标题、把手和入口均为 button；打开后焦点进入标题按钮，Escape 后 `is-open=false`、`aria-hidden=true`、触发器 `aria-expanded=false`，焦点返回 `referenceDrawerTrigger`。抽屉为命名的 complementary region，不伪装成阻塞式 modal。

### 6. “我的表单”审计目标识别错误且移动端溢出

旧 `my_forms.html` 不是实际入口；`/user/my_forms` 会重定向到 `/user/listening_registration?tab=records`，实际模板是 `_records_panel.html`。

处理真实模板后，390px 实测：表格宽 341px、父容器横向溢出 0、thead 隐藏、数据单元格为 grid，`表单ID/听课日期/课程名称/授课教师/听课地点/当前状态/版本数/最后更新/操作` 均由 `data-label` 提供移动字段语义。桌面仍保持表格，版本历史仍保持真正的表格关系。

### 7. 权限与反馈一致性

`permission_feedback.py` 现在支持动作语义，并在保持现有 JSON 字段、403、重定向和授权边界的前提下，把本轮确认的用户编辑/删除、管理页和统计入口拒绝分支迁移到统一帮助函数。经理角色不再看到仅超级管理员可访问的课程入口；浏览器以经理账号实测侧栏只包含今日工作、听课与填报、表单审核、人员与部门、统计与导出。

服务端 Flash 原来带 `data-bs-dismiss="alert"`，但祖先不是 Bootstrap `.alert`，关闭按钮实际无效。本轮改为 `data-app-message-dismiss`，并复用动态消息的删除行为；浏览器实测消息数从 1 变为 0。

## 误报与不应修改项

- 1440px 的审核筛选双列不是问题；真正问题是规则没有感知容器。最终实现保留窄栏双列，同时让中屏全宽区三列、手机单列。
- 登记统计属于课程与登记子流程，不强制并入管理员统计导航。
- `manage_groups` 的同路由 fragment 刷新已保留服务端渲染规则，不存在写入后根据不完整 JSON 重建卡片的问题。
- 版本历史、桌面密集统计等关系型数据继续使用表格；本轮只把移动主任务中的宽表转成记录卡/字段组。
- 数值 `0` 是有效结果，不等同于 EmptyState。
- 没有引入新配色、新字体、新 UI 框架或第二套设计系统。

## 浏览器验收矩阵

隔离服务使用 5097 端口、独立运行时数据库与合成账号；只对隔离数据执行可逆写入。

| 页面 / 行为 | 视口 | 结果 |
| --- | --- | --- |
| 审表考评统计 | 1440 / 1024 / 390 | 共享页头与子导航；390px 筛选单列；页面溢出 0 |
| 交表数量统计 | 1024 / 390 | 共享页头与子导航；工具区/结果区在 390px 单列；页面溢出 0 |
| 部门月度考评 | 1024 / 390 | 共享页头与子导航；页面溢出 0；宽结果保留局部 table-responsive |
| 登记统计 | 1024 / 390 | 共享 PageHeader、保留课程上下文、无统计子导航；页面溢出 0 |
| 审核队列筛选 | 1440 / 1024 / 390 | 2 / 3 / 1 列；页面溢出 0 |
| 审核详情 | 1280 / 390 | 七个 section、无嵌套卡片；主/危险/次操作层级正确；页面溢出 0 |
| 移动评分 modal | 390 | 字段纵向堆叠，每字段 314px；页面溢出 0 |
| 参考数据抽屉 | 键盘 | 打开、ARIA 同步、Escape 关闭、焦点返回均通过 |
| 我的表单 | 390 | 父容器溢出 0；九列主记录转为有标签的移动记录卡 |
| 经理导航 | 1280 | 不展示超级管理员课程入口 |
| 服务端 Flash | 1280 | 点击关闭后消息节点从 1 变为 0 |

## 截图证据

- [统计页改前 1440×900](screenshots/2026-08-14-final-convergence/before-review-assessment-stats-1440x900.png)
- [统计页改后 1280×720](screenshots/2026-08-14-final-convergence/after-review-assessment-stats-1280x720.png)
- [统计页改后 390×844](screenshots/2026-08-14-final-convergence/after-review-assessment-stats-390x844.png)
- [审核队列改后 1280×720](screenshots/2026-08-14-final-convergence/after-review-queue-1280x720.png)
- [审核表单改后首屏 1280×720](screenshots/2026-08-14-final-convergence/after-review-form-top-1280x720.png)
- [审核表单改后操作区 1280×720](screenshots/2026-08-14-final-convergence/after-review-form-actions-1280x720.png)
- [移动评分改前 390×844](screenshots/2026-08-14-final-convergence/before-review-scoring-modal-390x844.png)
- [移动评分改后 390×844](screenshots/2026-08-14-final-convergence/after-review-scoring-modal-390x844.png)
- [我的表单改后记录卡 390×844](screenshots/2026-08-14-final-convergence/after-records-cards-390x844.png)

## 验证

- 新增收口契约先出现真实 RED，再完成最小 GREEN；后续浏览器发现的容器阈值问题同样补测试后修复。
- Jinja：60 个模板全部编译通过。
- JavaScript：`app/static/js` 一方脚本全部通过 `node --check`。
- `git diff --check`：通过，仅有 Windows LF/CRLF 提示。
- 新收口与导航契约：26 项通过。
- 全量回归：417 项，314.927 秒，全部通过；仅输出既有 SQLAlchemy `Query.get()` LegacyAPIWarning 与测试刻意触发的 DeepSeek 失败/重试日志。

## 剩余风险与明确延后

- 全仓仍有一批旧页面的 inline style，其中不少与显示/隐藏、动态宽度或成熟页面特定行为绑定。本轮清除了已确认的 Bootstrap 4 类名和自定义 `btn-xs`，但没有在缺少逐页运行时证据时机械清零所有 inline style。
- 管理 API 仍有历史形成的资源专用拒绝文案；它们的响应结构和前端消费者并不完全一致。本轮只迁移已逐条核实且有测试覆盖的分支，没有用一次全局替换改变未知调用方的错误消息契约。
- 宽统计明细仍可能需要局部横向滚动，这是保持列间比较关系的有意策略；页面本身没有横向溢出。
- 浏览器截图只能证明本次合成数据与当前状态；权限边界、业务写入和生产数据密度仍由路由/单元回归和上线前人工验收共同覆盖。
