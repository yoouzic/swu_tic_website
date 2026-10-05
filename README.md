# SWU TIC 教学信息中心听课管理系统

面向信息员、部门管理员和中心审核人员的听课填报与管理网站。围绕“听课登记与填报 → 人工初审 → 中心复审 → 统计考核”组织工作，并提供当前课表查询、填表助手、现场线索辅助和自动审核建议。

后端使用 Flask、SQLAlchemy，默认存储为 SQLite；页面使用 Jinja、Bootstrap 5 和 JavaScript。异步审核使用 Celery/Redis，门牌 OCR 使用本地 RapidOCR/ONNX Runtime。

当前完整版本位于 `main`。仓库包含应用、测试和工具；业务数据库、私有课表、照片、密钥及本地生成结果不随代码分发。

## 主要功能

- **角色工作台**：按个人填表、审核和管理权限提供入口，展示待办、草稿和历史记录。
- **听课登记与表单**：关联具体课程身份，支持手动填写、草稿保存、驳回重填和版本历史；重复提交与并发编辑受事务保护。
- **当前课表与填表助手**：根据日期、教室或教师查找课程，人工确认后填入基础信息。评价以及两名见证人的姓名、手机号仍由填表人填写。
- **现场线索辅助**：启用后支持门牌照片、OCR、定位和楼栋地图，帮助查找与核对课程；照片记录和草稿可继续补完，候选结果需要人工确认。
- **人工审核与自动检查**：保留审核评分、意见和版本；规则检查及可选 DeepSeek 分析提供分类、证据和建议，最终通过或驳回由有权限的审核人员决定。
- **人员管理与考核**：部门、小组、人员及联系人管理，听课数量、审表考评、按学期的请假与考核规则、月度统计和 Excel 导入导出。

没有当前课表时，系统保留手填、草稿、历史记录、人工审表和后台设置，隐藏课程候选、查课、新登记及现场拍照辅助入口。导入当前课表后，候选索引和登记映射分别就绪，相应功能才恢复；历史课表不能代替当前学期的数据。

## 角色与审核流程

| 角色 | 主要用途 | 个人提交听课表 |
|---|---|---|
| 信息员 | 填报、登记、查看本人记录 | 默认允许 |
| 管理员 | 按授予的范围审核、管理和统计 | 需要有效的 `填表` 权限 |
| 超级管理员 | 系统、学期、课表、人员、考核配置及中心级审核 | 不允许以超管身份个人提交 |

人工审核分为两个阶段，小组级与部门级属于同一个初审阶段：

```text
待审核 → 小组级或部门级初审 → 部门已审核 → 中心级复审 → 中心已审核
   └──────────────── 驳回后由本人修改、重新提交 ────────────────┘
```

小组、部门和中心按对应组织范围审核，不能审核自己的表单。学期、当前课表和教学日历由超级管理员配置，信息员不用自行选择学期或数据来源。

## Windows 本地快速启动

仓库的 Python 配置为 **3.10.8**（`.python-version`、`Pipfile`）。在项目根目录执行：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-capture.txt
.\无课表测试.cmd start
```

`requirements-capture.txt` 包含主应用依赖及 Pillow、OCR 依赖。两个 Windows 启动器都会开启现场功能；无课表初始化也需要 Pillow 生成示例门牌图。

### 没有课表：无课表测试

默认地址：**http://127.0.0.1:5088**。也可以直接双击 `无课表测试.cmd`。

首次启动创建独立测试数据库、测试账号和模拟教学日历，不导入课表。再次启动保留表单、密码、草稿和后台设置。测试账号及完整操作流程见 [无课表测试说明](无课表测试说明.md)。

```powershell
.\无课表测试.cmd status
.\无课表测试.cmd restart
.\无课表测试.cmd stop
```

此配置仅监听本机，关闭外部 AI、队列服务连接和定时清理。之后可用测试超管导入课表，检查入口恢复及历史草稿行为。

### 有课表：普通本地调试

默认地址：**http://127.0.0.1:5087**。使用 `本地调试.cmd`，需要自行准备一份单学期全校课表。

```powershell
$env:LOCAL_DEBUG_SCHEDULE_FILE = 'D:\test-data\timetable.xlsx'
$env:LOCAL_DEBUG_FIRST_WEEK_MONDAY = '2026-03-02'
.\本地调试.cmd start
```

将示例路径和第一教学周周一改为该课表对应的实际值。工作区旧的春季测试课表不是仓库附件，新克隆不能依赖它；未知学期不配置教学日历会明确报错。重复启动复用已导入的同一文件。

普通调试使用 `data/instance/debug/` 和 `data/storage/debug/`；无课表测试使用 `data/instance/no-schedule-debug/` 和 `data/storage/no-schedule-debug/`，两者的数据库、进程记录与登录 cookie 分开。启动器支持 `LOCAL_DEBUG_PYTHON`、`LOCAL_DEBUG_PORT`、`LOCAL_DEBUG_RUNTIME_ROOT`、`LOCAL_DEBUG_STORAGE_ROOT` 指定解释器、端口和隔离目录。

## 自定义运行与部署

自行配置运行环境时，主应用依赖在 `requirements.txt`；需要现场拍照/OCR时再安装 `requirements-capture.txt`。复制 [.env.example](.env.example) 为 `.env`，设置 `SECRET_KEY`、数据库及存储路径。已有配置不要覆盖；进程环境变量优先于 `.env`。

Linux 本地开发示例：

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，设置本地密钥、数据库和存储目录后继续
python tools/init_user.py --student-id admin
FLASK_RUN_HOST=127.0.0.1 python -m app.app
```

`tools/init_user.py` 会交互请求密码，并在配置的数据库中创建或更新账号。应用初始化只创建表、默认部门和权限，没有内置正式账号。Flask 开发入口默认端口为 `5000`；WSGI 部署入口为 `wsgi:app`，例如 Linux 上使用 `gunicorn --bind 127.0.0.1:8000 wsgi:app`。首次部署还需先完成数据库及账号初始化。

| 配置 | 用途 |
|---|---|
| `FLASK_ENV`、`FLASK_DEBUG` | 运行环境及开发调试开关 |
| `SECRET_KEY`、`SESSION_COOKIE_SECURE` | 会话密钥；HTTPS 生产环境使用安全 cookie |
| `DATABASE_URL` / `SQLITE_DB_PATH`、`INSTANCE_DIR` | 数据库连接或 SQLite 路径、实例目录 |
| `UPLOAD_FOLDER`、`EXPORT_DIR` | 上传与导出目录 |
| `CONTACT_TEMPLATE_PATH`、`SCHEDULE_TEMPLATE_PATH` | 联系人及全校课表模板 |
| `LECTURE_CAPTURE_ENABLED` | 现场拍照功能开关，普通应用默认关闭 |
| `CELERY_BROKER_URL`、`CELERY_RESULT_BACKEND` | 自动审核任务队列与结果存储 |
| `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` | 可选外部模型配置 |

生产环境使用独立数据库、持久化存储、真实密钥和 HTTPS；Windows 演示账号、密码和模拟日历只属于隔离测试配置。手机相机与定位需要浏览器允许的安全上下文，普通局域网 HTTP 不能替代 HTTPS。

## 课表与自动审核数据

系统有两条不同的导入流程：

| 流程 | 格式及操作 | 用途 |
|---|---|---|
| 后台全校课表导入 | `.xls` / `.xlsx`；按 `SCHEDULE_TEMPLATE_PATH` 校验列名和顺序后导入 | 课程、教师、场地、权威课表快照、候选索引和登记映射 |
| 自动审核参考数据集 | 三类 `.xlsx`：学校课表、听课人班级映射、个人课表；先预览再启用 | 自动审核的参考数据与覆盖状态 |

自动审核参考数据集的“启用”不等于听课登记和助手所需的当前课表已就绪。常规导入前需准备对应模板，并在后台设置当前学期和教学日历。

自动检查不会自动审批表单。异步批次需要 Redis 和 Celery Worker；无课表演示入口不运行这条集成。启动 DeepSeek 检查前需要审核人员确认外传范围，密钥仅保存在服务端配置中。初始化、队列、数据集、重试、取消和恢复操作见 [自动审核运维说明](docs/AUTOMATED_REVIEW_OPERATIONS.md)。

## 代码结构与文档

```text
app/
  app.py               应用工厂、配置和初始化
  blueprints/          登录、用户与后台路由
  services/            课表、登记、表单、审核、考核及现场线索服务
  review_automation/   自动审核规则、数据集、批次和可选模型调用
  ui/                  导航与角色工作台
  templates/           Jinja 页面
  static/              样式、脚本和楼栋地图数据
tools/                 本地启动、初始化、迁移及回放工具
tests/                 Python、JavaScript 与浏览器回归
docs/                  设计、功能说明及提交索引
wsgi.py                WSGI 应用入口
celery_worker.py       Celery Worker 入口
```

- [无课表测试说明](无课表测试说明.md)
- [普通本地调试与历史试用记录](docs/LOCAL_DEBUG_LISTENING_ASSISTANT.md)
- [自动审核运维说明](docs/AUTOMATED_REVIEW_OPERATIONS.md)
- [楼栋对应与地图来源](docs/CAMPUS_BUILDING_MAPPING.md)
- [现场线索融合模型记录](docs/SITE_CONTEXT_FUSION.md)
- [功能提交与验证索引](docs/GIT_FEATURE_COMMITS_2026-10-05.md)

设计和试用记录保留各阶段的历史决策；当前功能以 `main` 中的实现和测试为准。
