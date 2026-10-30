# 听课辅助整套功能的用户灰度开放设计

日期：2026-10-13。状态：备选设计，尚未实施。用户随后选择优先考虑独立测试站 `test.yoouzic.top`，灰度改造暂不进入实施。

本次授权范围是深入分析、设计和规划。这里的 API、数据表、能力字段均为拟议合同；当前应用尚未实现这些合同。全量开放由用户测试后手动操作，代码部署、开关调整和消息通知是不同动作。

## 1. 已确认的产品目标

用户已确认将拍照、定位、地图、填表助手、课程查询、听课登记作为一个整体，只有一个测试资格。默认其余用户不能使用，邀请人员通过后台手动授权。验证正常后，后台一次切换到全量开放。

- 包内含门牌照片、OCR 与区域重识别、定位采集、楼栋地图、现场线索推荐、课程候选问答与确认、评价快捷问答、课程查询、新听课登记与登记辅助。
- 普通手填、普通草稿、个人已提交表单、版本历史、人工审核、统计和后台课表维护继续按原有权限运行。
- 授予测试资格不能改变角色、填表权、审核权、管理范围或账号是否在任。
- “整体”指共享一份资格。课表候选索引、登记映射、拍照服务器配置等技术前提仍分别检查。
- 不增加百分比放量、自动邀请、自动全量发布、推送通知或 WebSocket。全量切换在用户下次打开页面/发送请求时生效；旧页面保留输入并提示重新核对。

## 2. 当前代码证据

分析目录：`SWU_TIC-submit`。确认的最新基线：`d42881b2e596c1f201da83a35a08352f09221bdd`，其中已包含本次分析期间由其他工作提交的课表修复。实施前重新检查 HEAD 和工作区，不把此次设计文档与别人的改动混合提交。

| 位置 | 当前行为 | 本次需要解决的联动 |
| --- | --- | --- |
| `app/services/schedule_availability.py:8` | 拍照页面以 `LECTURE_CAPTURE_ENABLED && candidates_ready` 判断 | 加入用户资格；保留纯课表状态的含义 |
| `app/blueprints/user/forms.py:55`、`:494`、`:519`、`:645` | 页面、草稿投影、草稿保存、提交分别调用拍照判断 | 四处必须使用同一能力服务，避免界面手填而提交要求照片 |
| `app/blueprints/user/forms.py:691` | 新拍照表单要求照片、拍摄日期和教室一致 | 普通用户不进入照片约束；测试用户保留现有约束 |
| `app/blueprints/user/forms.py:723` | 助手 payload 可独立触发候选复验 | 不能只关闭拍照；普通用户伪造或恢复助手状态也不能激活辅助路径 |
| `app/blueprints/user/forms.py:813` | 登记绑定参与课程匹配和审核标签 | 灰度资格与既有绑定分开处理，防止旧页提交绕过灰度 |
| `app/blueprints/user/site_capture.py:25` | `_enabled()` 仅检查服务器拍照总开关 | 新动作必须检查灰度；已有本人证据只读单独处理 |
| `app/blueprints/user/listening_assistant.py:114`、`:434` | 助手 API 验证账号和当前课表，不验证灰度 | 给五个助手端点加统一资格判断 |
| `app/blueprints/user/course_lookup.py:13` | 所有活跃账号可直接访问课程查询页面 | 隐藏入口并拒绝未授权直接访问 |
| `app/blueprints/user/reservations.py:125`、`:152`、`:294` | 新登记依赖课表映射 | 增加资格；记录页本身继续可访问 |
| `app/app.py:207`、`app/ui/navigation.py:166`、`app/ui/workspace.py:29` | 导航与首页快捷入口直接消费全局课表状态 | 使用当前用户的能力，不能只改填表模板 |
| `app/templates/user/lecture_form.html:255`、`:1296` | 评价快捷问答和脚本独立于课程助手加载 | 一起纳入辅助功能资格；保留原始评价字段和基础校验 |
| `app/blueprints/admin/courses.py:46` | `/admin/api/courses` 仅要求登录，返回课程列表 | 关闭普通账号绕过课程灰度的入口，管理接口限定超级管理员 |
| `app/templates/admin/course_feedback_management.html:1383` | 后台模板调用用户的登记历史接口 | 分离后台只读用途；用户接口加灰度不能破坏后台管理 |
| `app/blueprints/admin/users.py:1170` | 编辑旧权限会将普通用户设为管理员 | 独立的测试名单管理接口，不复用该写接口 |
| `app/models.py:560` | `SystemSetting.value` 长度为 255；`set()` 会立刻提交 | 模式可复用此表，整份名单不塞入一个 JSON 值；写操作自行控制事务 |
| `app/services/lecture_form_draft_entry_schema.py` | 服务器记录 `manual/photo/legacy` 草稿策略 | 复用手填草稿的兼容机制，不让客户端自授手填豁免 |
| `app/services/lecture_form_draft_concurrency.py` | CAS 保护原始草稿 JSON 与版本 | 授权变化后的投影/保存不能覆盖另一个照片草稿 |

本次通过源码和 AST 核对路由与装饰器，没有启动应用、读取真实账号名单、修改运行库或执行功能测试。实际相机、GPS、OCR 和真实课表匹配不属于本次已验证结果。

## 3. 方案比较

| 方案 | 优点 | 成本和问题 | 结论 |
| --- | --- | --- | --- |
| 独立授权表 + 系统模式 | 有稳定用户关联、可审计、删除可清理、扩容简单 | 新建小表和迁移工具 | 推荐 |
| 为每个用户存一条 `SystemSetting` | 初版代码少、无需改用户表 | 无外键；用户 ID 复用和删除需额外补救；审计表达不清晰 | 适合短期原型，本次不用 |
| 增加旧 `Permission/RolePermission` 权限 | 界面看似可复用 | 旧编辑接口改变角色，默认权限回退和超管绕过易干扰灰度 | 不采用 |

只做一个固定业务包 `listening_tools`，不建设通用实验平台。新增表在 `app/models.py` 中定义；授权、能力、变更事务放入窄服务模块，避免继续扩大已有填表路由文件。

## 4. 开放模式与授权规则

`SystemSetting` 中保存 `listening_tools_mode`：

| 模式 | 产品文案 | 使用资格 |
| --- | --- | --- |
| `off` | 暂停开放 | 没有用户获得新功能资格 |
| `pilot` | 指定用户测试 | 仅显式授权的活跃账号 |
| `all` | 全量开放 | 所有活跃账号；原有角色限制继续生效 |

首次初始化为 `pilot`，名单为空；不会自动把超级管理员、开发者或某个编号加进去。模式缺失按 `pilot` 处理；模式非法、授权表未迁移或授权记录异常时拒绝开放，并向设置管理员显示原因。既有模式和名单不会被每次启动重置。

规则顺序：从数据库取得真实用户 → 在任检查 → 模式与授权 → 原有业务权限 → 数据/环境前提。`session.user_role`、请求头、查询参数、JS 状态和旧助手 payload 都不能授予资格。超级管理员可管理名单和后台课表，但个人使用用户侧辅助也遵守灰度；超级管理员仍没有个人填表权。需要完整拍照填表测试时，使用具有原有填表权限的测试账号并给该账号授权。

全量开放只移除逐个邀请要求，绝不把所有账号变成可填表者。

## 5. 数据与服务合同

### 5.1 持久化

- `SystemSetting.key='listening_tools_mode'`：`off/pilot/all`。
- `SystemSetting.key='listening_tools_revision'`：全局模式版本 UUID，只在模式改变时更新。
- `ListeningToolsGrant` / `listening_tools_grants`：`user_id` 为主键并外键关联用户（`ON DELETE CASCADE`）；`is_enabled`；`revision` UUID；`updated_by` 可空整数；创建和更新时间。
- `ListeningToolsAccessEvent` / `listening_tools_access_events`：追加记录授权、撤销、模式变化、离任撤销和删除清理。保存动作、操作者 ID、对象 ID、对象账号创建时间、前后模式/授权值、版本与时间。操作者和对象采用可空整数快照，不建立阻碍账号删除的外键；不保存密码、照片、GPS 或整份表单。

`updated_by` 与事件中的 ID 只用于审计，不参与权限判断。授权表按用户显式删除，不能仅依赖 SQLite 的外键开关。离任时撤销授权；重新启用账号后需重新邀请。移动部门/小组、编辑其他权限不会自动授予或删除仍有效的测试资格。用户物理删除后，旧 ID 再利用不能继承授权。

迁移只创建这两张小表和缺失的两项设置，不重写 `users`、表单、照片或草稿。提供可重复执行的 Flask CLI：`listening-tools init-schema`。迁移和部署步骤在实施验收后再执行；本次不运行。

### 5.2 能力解析

新增 `app/services/listening_tools_access.py`，提供：

- `get_listening_tools_access(user)`：只解析账号、模式、授权与版本，不访问课表。
- `get_listening_tools_capabilities(user, *, availability=None)`：把资格与当前课表、环境、填表权限组合。
- `update_listening_tools_mode(actor, mode, expected_revision)`、`update_listening_tools_grant(actor, target, enabled, expected_policy_revision, expected_grant_revision)`：校验超级管理员、并发版本和目标状态，在调用方事务中写入，不自行 commit。
- `lock_listening_tools_policy()`：在需要保护功能写操作时锁定全局版本行并重新读取授权；外部 OCR、文件解码和候选计算不在该锁内执行。
- `revoke_departed_user_grant()`、`delete_user_listening_tools_grant()`：与账号生命周期共享事务。

返回不可变快照 `ListeningToolsCapabilities`：

| 字段 | 意义 |
| --- | --- |
| `allowed`、`mode`、`revision`、`reason` | 灰度资格、模式、该账号可用状态版本和内部原因 |
| `lookup_enabled` | 资格且候选索引 READY |
| `assistant_enabled` | 资格且候选索引 READY；只读候选不授予填表权 |
| `completion_enabled` | 资格且具有填表权限；评价快捷问答不依赖课程索引 |
| `capture_enabled` | 资格且填表权、服务器拍照开关、候选索引 READY |
| `registration_enabled` | 资格且填表权、当前登记映射 READY |

`revision` 由全局模式版本及该账号授权版本组合而成；邀请另一个人不使当前测试用户的页面无故失效。请求级可复用一次快照，跨请求和跨进程不能缓存用户授权。进入短写事务后重新读取，不使用 ORM identity map 中的旧授权。

继续保留 `current_schedule_availability()` 的全局数据含义。后台导入、人工审核和自动审核数据源继续消费原始数据状态，不把某用户的灰度资格写入课表状态。

## 6. 页面和路由覆盖清单

下面的路径均为现存路径；新增后台灰度 API 在第 8 节定义。

| 页面/接口 | 新访问规则 |
| --- | --- |
| `/user/submit_form`，编辑、重填页面 | 保留原有填表权；模板按能力显示整套辅助 |
| `/user/api/lecture_form_draft` GET/PUT/DELETE | 保留原有草稿读写规则；按能力投影/保存辅助元数据 |
| `/user/course_lookup` | 登录、灰度资格、候选索引；未授权直接访问返回 403 说明页 |
| `/user/api/listening-assistant/guide/start`、`guide/answer`、`candidates`、`fallback`、`confirm` | 登录、灰度资格；保留当前课表复验及原响应合同，禁用的 fallback 仍不恢复历史课表 |
| `/user/api/site-capture/settings` | 拍照能力 |
| `/user/api/site-capture` POST | 拍照能力、原有填表权；在保存照片和草稿前再次检查 |
| `/user/api/site-capture/<id>` PATCH | 拍照能力、原有填表权、本人记录 |
| `/user/api/site-capture/<id>/map` | 拍照能力、本人记录 |
| `/user/api/site-capture/<id>/ocr-region` POST | 拍照能力、原有填表权、本人记录；OCR 完成后更新前再次检查 |
| `/user/api/site-capture/<id>/location` PATCH | 拍照能力、原有填表权、本人记录 |
| `/user/api/site-capture/<id>/suggestions` GET/POST | 拍照能力、本人记录、原课表检查 |
| `/user/api/site-capture/<id>/confirm` POST、`/<id>/resume` POST | 拍照能力、原有填表权、本人记录、原草稿 CAS |
| `/user/api/site-capture/<id>` GET、`/<id>/photo` GET、`/records` GET | 已存在的本人证据只读；撤销资格后可保留读取，不呈现新拍照/恢复/推荐操作 |
| `/user/listening_registration`、`/course_feedback_management` 旧入口 | 页面继续可访问；未开放时强制记录页，不显示新登记区 |
| `/user/api/available_courses` | 登录、灰度资格、登记映射；不以灰度授予登记权 |
| `/user/api/create_reservation` POST、`/my_reservations/<id>` PUT | 灰度、原有填表权、登记映射、本人约束及禁听/限额检查 |
| `/user/api/unused_reservations` | 未开放时返回现有空数组合同；不启动课表预填流程 |
| `/user/api/course_registration_history` | 原填表权与灰度；此接口可查其他人的课程登记，不能当作本人历史例外 |
| `/user/api/my_reservations` GET | 本人历史，保留现有只读权限 |
| `/user/api/my_reservations/<id>` DELETE、`/cancel_reservation` POST | 保留原有本人未绑定记录清理；不准借此新增或修改登记 |
| `/user/api/time_suggestion` | 基础教学日历提示，保留共享只读行为；它不查询课程，后台也使用 |
| `/admin/api/courses` GET、`/admin/api/courses/columns` GET | 改为超级管理员的后台管理接口，不向普通灰度用户开放旧后台入口 |
| 后台课表导入、课程管理、禁听、审核、统计等其他端点 | 沿用原业务权限，不由测试资格授权，也不因普通用户灰度关闭而失效 |

模板同时覆盖侧栏、首页快捷操作、活动中心、空状态提示、登记修改按钮、填表页 DOM/脚本、后台课程页面的用户侧查询链接。全局纯数据字段不作为页面的使用资格。

评价快捷问答纳入包内后，未开放账号不加载 `form-completion-assistant.js`，但普通评价字段、反馈字数、见证人、手机格式和服务端字段校验保持可用。不能将基础验证误当成灰度功能关闭。

## 7. 草稿、旧页面和事务边界

### 7.1 用户尚未获得资格

展示完整普通表单；新表单不要求照片。草稿 GET 仅向客户端投影普通字段，去除 `assistant`、`assistant_payload`、`site_capture_id` 的可执行状态；GET 不修改原草稿或照片。没有授权时不会通过客户端 metadata 激活候选复验、照片约束或自动课表预填。

PUT 保存普通字段时使用现有原始 JSON/版本 CAS，服务器设置 `entry_mode='manual'`，不修改原照片证据；返回规范化 payload 和最新能力以帮助旧客户端清理状态。客户端提交的 `entry_mode`、`manual_entry_allowed` 不可信。

`registration_id` 需单独处理：新手填表单不能仅凭客户端 ID 激活课表辅助；服务器草稿中已存在的本人登记绑定和已提交表单的既有绑定可按原有归属校验保留。未开放的新手填提交不以课表相似度新生成“无需人工审核”标签。不能删除旧绑定或改动已提交表单的历史标签。

### 7.2 从手填转入灰度/全量

复用服务器 `manual/legacy` 策略：已保存的合法手填草稿可继续手填到提交，不在中途强制补照片。只有草稿被消费或用户新建下一份时进入新的拍照策略；恢复照片草稿按现有规则撤销该手填豁免。

对“页面打开后尚未保存，管理员改变模式/资格”的情况，在页面附带能力版本。服务端发现版本变化而流程约束也变化时返回 409 并保留输入、提示重新打开核对；该版本只能用于发现变化，不能授予手填豁免。没有任何已保存草稿或可信旧页面依据的新请求，不能通过伪造旧版本绕过当前规则。

### 7.3 取消资格、暂停或课表不可用

取消资格后的新辅助 API 请求返回 403；课表/环境不可用有资格用户收到对应不可用说明。普通手填依然可用。个人已有照片/记录保留，照片恢复、定位、OCR、地图和候选查询不能继续启动。保留本人未绑定预约的删除操作，禁止新的预约修改。

旧前端收到 `listening_tools_not_enabled` 或 `listening_tools_changed` 时停止摄像头轨道、定位采样与后续请求，清理助手的可执行状态和预填入口，保留可见字段和草稿。普通页面不显示“申请测试”或实验配置细节。

### 7.4 并发的明确语义

读请求只保证开始解析权限时使用当前数据库状态；已经发给客户端的数据无法撤回。新功能写入与授权/模式更新用相同的全局策略版本行短锁序列化，写入临界区重新加载资格。锁顺序统一为策略 → 用户/原表单锁 → 草稿/照片/登记；所有新增生命周期清理遵循该顺序。

上传流程保持“照片与草稿提交在 OCR 前”。撤销发生在照片首次保存之前则不创建记录、不保留新文件；撤销发生在保存之后，已保存照片作为本人历史保留。上传已开始的机器 OCR 可以完成已有照片的机器证据收尾，仍遵循原有条件 UPDATE，不复活课程确认或覆盖新草稿。单独的区域重识别在 OCR 完成后重新检查资格，失效则不更新记录。

外部 OCR、定位等待、完整图像解码、课程匹配运算都在策略锁之外；提交中的辅助证据再次写入前核对版本。数据库支持的版本行锁/CAS用于跨 worker 同步，不能用进程内 Python Lock 代替。

## 8. 后台交互和 API

在“系统设置”增加“功能开放”页签，仅超级管理员可见、可操作。页面提供整套功能清单、三个开放模式、候选索引/登记映射/拍照配置状态、测试名单搜索和授权/撤销、最近变更记录。

名单显示现有账号的姓名、成员编号、部门、角色、在任状态和测试资格。支持按姓名/编号/学号搜索并分页；授权使用数据库用户 ID，不能根据重名或客户端角色自动推断。人数少，首版用单人明确授权，避免一次替换整份名单覆盖另一个管理员的修改。

| 新路径 | 方法 | 合同 |
| --- | --- | --- |
| `/admin/api/settings/listening-tools` | GET | 模式、全局版本、名单数量、依赖状态、是否完成迁移 |
| 同路径 | PUT | `{mode, expected_revision}`；仅支持三种模式 |
| `/admin/api/listening-tools/users` | GET | `q/page`，固定每页 20，返回候选账号及授权版本 |
| `/admin/api/listening-tools/users/<user_id>` | PUT | `{enabled, expected_policy_revision, expected_grant_revision}`；未创建记录用 `missing` |
| `/admin/api/listening-tools/events` | GET | 最近事件分页，只返回管理页所需字段 |

名单授权时目标必须在任；撤销允许处理已有离任授权。空名单不影响普通手填。`all` 模式下保留名单，以便退回 `pilot` 继续仅向原测试人开放。

切换全量时在页面明确显示受影响的活跃账号数与现有权限约束，再由管理员点击确认；全量是用户计划中的独立手动操作，测试通过不自动触发。退回 `pilot` 或 `off` 同样可操作，历史业务数据不删除。

所有写请求使用当前 CSRF。操作和审计事件同事务提交；失败回滚二者。重复设置相同值为幂等成功，不重复写事件。两个管理员同时编辑使用 expected revision，冲突返回 409 并提供刷新提示；不采用最后写入者覆盖。

统一错误：未登录 401；未在任沿用现有账号状态处理；未获得灰度 403 + `listening_tools_not_enabled`；权限不足 403；依赖未就绪 503；版本/草稿冲突 409；非法参数 400。JSON 保持 `{success, data, message, code}` 兼容字段；用户只看到中文说明。

## 9. 验收与开放流程

1. 在隔离库迁移，确认初始 `pilot + 空名单`。有真实候选/登记 fixture 时，普通账号仍只能手填，不能从旧 API 查课程。
2. 由超管手动邀请具有原有填表权的测试账号；无需重登即可在新请求看到变化，其他账号仍未开放。
3. 测试完整路径：照片保存 → OCR/手动门牌 → 定位/地图 → 候选核对 → 评价/见证人 → 保存/恢复 → 提交 → 审核；以及课程查询、登记、预填与限额。
4. 测试取消资格、课表切换、旧页提交、手填草稿延续、照片草稿恢复、并发保存、两个管理员修改、离任与 ID 复用。
5. 桌面与 390px 浏览器验证入口和请求；手机通过实际安全上下文验证相机/GPS。模拟 OCR 与 GPS 可验证联动，但不能据此宣称真机能力通过。
6. 在真实用户测试确认正常后，由用户决定何时手动切换 `all`。重新打开页面后所有活跃用户按原业务权限获得相应功能；保留测试名单与开关用于回退。

功能回退使用 `pilot/off`。直接部署灰度改造以前的代码可能恢复“有课表就对所有人开放”的旧逻辑，不能当作等价的功能回退。

## 10. 改动规模与实施边界

预计涉及现有服务/路由/导航/模板/JS 约 15—20 个应用文件，新增约 6—8 个窄模块/模板/脚本，以及授权、路由、草稿和浏览器测试。最终文件数以实施时拆分为准。主要成本在跨入口一致性与历史/并发回归；课程匹配、OCR、地图和表单主体可继续复用。

本次不改课表解析算法、课程身份规则、审核阶段、分数计算和原角色矩阵。不给用户设置真实测试资格，不启动生产迁移，不部署，不全量切换，不通知其他人。

设计中的实现细节为备选方案。本次不继续编写灰度代码实施计划：用户已转向独立测试站，测试站的实测容量和隔离设计见 `docs/superpowers/specs/2026-10-13-test-site-design.md`。如果日后决定在正式站内逐人放量，可从本设计继续。
