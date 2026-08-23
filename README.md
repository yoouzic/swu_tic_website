# 自动审核 Task 15 运维说明

自动审核是“分类、标记、证据和建议”工具，不是审批人。它不会自动通过或驳回表单，也不会写入 `LectureForm.status`、`reviewer_id`、`review_time` 或 `review_comment`。最终决定必须沿用现有人工审核页面完成。

## 环境变量

生产环境请通过受保护的进程环境或密钥管理器注入变量，不要把密钥写入仓库、命令历史、日志或批次 payload。

- `SQLITE_DB_PATH`、`DATABASE_URL`、`INSTANCE_DIR`：应用数据库和实例目录。生产数据库必须由部署配置明确指定。
- `CELERY_BROKER_URL`、`CELERY_RESULT_BACKEND`：Redis broker/result backend；生产使用专用 Redis 数据库或实例，禁止复用测试数据。
- `DEEPSEEK_API_KEY`：DeepSeek API 密钥，仅服务端读取。
- `DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`：外部 API 地址和模型；默认模型为 `deepseek-v4-flash`。
- `DEEPSEEK_THINKING_ENABLED`、`DEEPSEEK_REASONING_EFFORT`：思考开关和推理强度。
- `DEEPSEEK_TIMEOUT_SECONDS`、`DEEPSEEK_MAX_RETRIES`、`DEEPSEEK_MAX_CONCURRENCY`：请求超时、重试和并发上限。
- `DEEPSEEK_PROMPT_VERSION`：结构化提示词版本，用于审计和指纹。
- `AUTOMATION_UPLOAD_DIR`、`AUTO_REVIEW_UPLOAD_DIR`、`UPLOAD_FOLDER`：自动审核和通用上传的隔离目录。
- `CELERY_TASK_ALWAYS_EAGER`：仅限测试或本地诊断；生产 worker 必须使用正常 Celery 队列。

## 存量 CourseRegistration 时区迁移（一次性）

历史版本的 `CourseRegistration.created_at` 使用 SQLite `CURRENT_TIMESTAMP`（UTC），与业务侧 `datetime.now()`（UTC+8）不一致。数据库备份后，可执行：

```powershell
python tools/migrate_course_registration_utc8.py             # 默认 dry-run
python tools/migrate_course_registration_utc8.py --apply      # 真正迁移 UTC -> UTC+8
```

脚本使用 `SystemSetting` marker 幂等保护；重复运行不会重复加 8 小时。默认 dry-run 只打印将调整的记录。

## 初始化与启动

在目标 checkout 中使用与部署一致的 Python 环境：

```powershell
python -m flask auto-review init-schema
docker run --rm --name swu-tic-review-redis -p 6389:6379 redis:7-alpine redis-server --save "" --appendonly no
redis-cli -h 127.0.0.1 -p 6389 -n 15 ping
$env:CELERY_BROKER_URL='redis://127.0.0.1:6389/15'
$env:CELERY_RESULT_BACKEND='redis://127.0.0.1:6389/15'
python -m celery -A celery_worker.celery_app worker --loglevel=INFO --pool=solo
```

上面的 Redis 命令只适合本地/CI 的 disposable 实例：使用独立端口和 DB，检查应返回 `PONG`；也可用受控的 `redis-server --port 6389 --save "" --appendonly no` 替代容器。生产 Redis 必须由运维托管并使用专用实例、认证和备份策略，不能照搬临时端口或连接测试 DB。先确认应用、worker、Redis 使用的是同一组环境变量。启动后在“系统设置 → 自动审核”检查 Redis、Celery Worker 和 DeepSeek 状态，再从审核队列预览批次。生产 Redis、数据库和上传目录必须与临时验证环境隔离。

## 课表上传

支持学校课表、听课人班级映射和个人课表三类 `.csv`、`.xls`、`.xlsx` 文件；模板首选 CSV 或 XLSX-ready 格式，字段名和列结构必须遵循对应模板。每个文件都必须先上传并查看 staged preview，确认学期、有效行和问题数后再点击启用；启用操作会形成可追溯的数据集版本。覆盖状态可能为完整、基础或缺失，缺失不会被伪装成无风险。

## DeepSeek 外传警示

启动 LLM 检查前，审核人必须确认：完整表单及相关证据可能发送到 `DEEPSEEK_BASE_URL` 指向的外部 API。没有确认时按钮保持不可用。请只使用获得授权的合成或业务数据，避免在日志中记录 API key、原始表单、手机号、意见内容或模型推理文本；错误只应保留可审计的错误代码和隔离状态。

## 批次、重试与取消

批次只传递表单 ID；服务端重新读取表单、数据集和规则快照，并按指纹复用已有 assessment。完成、部分失败、取消和失败状态都应以批次状态及数据库计数为准。单个 DeepSeek 或规则错误必须保持隔离，不能把失败伪分类为“无明显风险”。

需要重试时，在审核队列重新选择明确的表单 ID，重新预览并启动批次；重复指纹会复用缓存。需要停止时使用受保护的取消 API/运维动作（`POST /admin/api/automation/batches/<batch_id>/cancel`），然后等待状态聚合为已取消或已完成但有失败；不要假定批次页面已有取消按钮。不要手工删除 assessment 或改写历史。排障时可停止 worker 后安全重启，任务会按 ID 和指纹幂等恢复。

## 回滚

1. 停止新批次并取消仍在排队的批次，确认没有继续运行的 worker 任务。
2. 在自动审核设置中禁用当前规则，保留 revision 历史；必要时重新启用上一个已审计版本。不要删除规则、assessment 或批次历史。
3. 将课表数据集切回上一个已验证版本，或暂时停用对应数据集；保留 staged/active 变更记录。
4. 如需整体下线，停止 Celery worker，并按部署方式禁用自动审核 UI/路由注册；既有表单和人工审核表保持不变。
5. 从受控备份恢复前先核对数据库、上传目录和 Redis 的目标，恢复后验证人工审核字段、assessment 指纹和批次状态；禁止用回滚覆盖人工审核决定。

任何自动化结果都只能作为人工复核线索。通过和驳回始终由授权审核人点击既有人工动作完成。
