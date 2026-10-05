# UI 配色与质感升级交接

## 1. 当前交付状态

- 日期：2026-09-19。
- 分支：`agent/ui-ux-third-pass-finalization`。
- 实现提交：`9650f16` — `feat: refine campus UI with paper and ink styling`。
- 已在本地提交，未推送远端。该分支还有此前的未推送提交，接手时先检查 `git log @{upstream}..HEAD`，不要直接推送全部历史。
- 最后一轮相关回归：**138 项测试通过**，包含 11 项真实 Chromium 样式测试；`git diff --check` 通过。
- **完整逐页人工验收未完成**。用户因额度限制要求收尾，请勿把测试通过等同于全站视觉与交互验收通过。

## 2. 已确认的设计方向

用户希望界面更优雅、青春现代，允许轻量动效与适量纹理。试做的浅青绿主题被用户认为“太像 AI”，最终明确选择：

| 用途 | 颜色 |
| --- | --- |
| 纸白背景 | `#F4F2EC` |
| 墨蓝灰主色 | `#465A70` |
| 深墨蓝强调 | `#2B3C50` |
| 旧金点缀 | `#D8C5A2` |
| 内容表面 | `#FFFEFA` |

方向是哑光纸张、蓝黑墨水、少量黄铜书签的材质联想，不是高饱和科技蓝或大面积渐变。背景保留静态细颗粒；登录展示区使用淡横线纸纹。表格、输入区保持干净，成功/警告/危险颜色继续表达业务状态，不要统一替换为品牌色。

## 3. 实现范围与入口

| 文件 | 作用 |
| --- | --- |
| `app/static/css/style.css` | 主题变量、纸纹、导航、公共控件、登录/工作台、响应式、动效 |
| `app/templates/auth/login.html` | 登录页校园文案、书签标识、标题、页脚；保留原认证表单 |
| `app/templates/main/workspace.html` | 各角色共享工作台展示与区块图标 |
| `app/templates/partials/_sidebar.html` | 品牌标识、用户首字头像与身份布局 |
| `tests/test_campus_theme_browser.py` | 新增真实浏览器计算样式与 390px 布局测试 |
| `tests/test_unified_shell.py` | 旧固定色值断言改为主题映射断言 |
| `tests/test_ui_targeted_audit_contracts.py` | 移除已被卡片布局替代的旧分隔线断言；真实浏览器测试接管该布局检查 |
| `tests/test_workspace_routes.py` | 登录测试携带真实 CSRF token，并隔离各角色的应用上下文 |

未修改业务后端、权限、数据库结构、API、认证逻辑，也未新增前端框架或动画依赖。

## 4. 已验证与未验证

已执行：
- 桌面登录页外观检查。
- 手机登录错误提示、正确登录、密码显隐、登录帮助弹窗开关。
- 查看手机“听课与填报”页面。
- 浏览器测试覆盖：导航/主按钮/占位文字对比度、登录点击区域、一次性入场、减少动态效果、纹理不阻挡点击、文字按钮主题、紧凑表格、抽屉过渡、390px 指标卡片。
- 相关 UI 合约、公开页面、各角色工作台路由等共 138 项回归。

下一位优先完成：
1. 信息员、管理员、超级管理员实际工作台的桌面与手机逐页检查。
2. 移动导航的打开/关闭、Tab 焦点、Escape、关闭后焦点恢复；用户菜单与弹窗的键盘操作。
3. 听课登记、填报、记录、审核队列及详情的正常数据、空状态、长文本、校验错误。
4. 390、768、960、1024、1440px 下的溢出、表格密度、固定操作栏与弹窗层级。
5. 统计与其他未逐页检查的页面是否仍有不协调的硬编码配色；保留状态色语义。

这些是待验收项，不是已经确认的缺陷。浏览器工具曾出现间歇性调用失败；一次只对同一页面执行一个操作、重新获取 snapshot 后使用新 uid 较可靠。未完成最后的工作台截图验收，不应引用截图文件名作为验证证据。

## 5. 技术注意事项

- 项目是 Flask/Jinja 服务端模板与原生 JS，实际本地 Bootstrap 为 **5.1.3**。不要假设 5.3 的按钮或 offcanvas CSS 变量会被消费；本轮相应新增样式已使用显式属性。
- 入场动画使用 `backwards`，结束后释放 transform；不要在整个主内容容器上保留 transform，以免影响固定元素与层级。
- `prefers-reduced-motion` 同时清零延迟并压缩动画/过渡时长。
- 占位文字必须保持 `opacity: 1`，避免纸白底上的文字对比度再次不足。
- 不要用全局单元格 padding 覆盖 `.table-sm`；本轮已移除导致紧凑表格变高的覆盖。
- 新浏览器测试使用真实 CSS 与组件夹具，不加载 Bootstrap JS，不能代替真实页面的弹窗/菜单交互验收。

## 6. 本地预览与数据安全

本次隔离预览地址为 `http://127.0.0.1:5093/`，仅在原会话的预览进程仍运行时有效，不是正式部署。

- 合成账号：`super`、`manager`、`user001`；预览密码均为 `1234564`。仅用于临时测试环境，不能作为正式账户配置。
- 预览使用系统临时目录下的独立 SQLite、上传与导出目录，关闭定时清理与外部 LLM 调用；没有使用业务库或备份库。
- 运行进程开启 Jinja 模板自动重载；否则 CSS 更新后，HTML 文案可能仍显示缓存版本。
- `tools/prepare_local_debug.py` 会创建测试账号并重置目标库里的管理员密码。**不要直接对现有数据库执行。**
- 应用导入时会读取 `.env`。重建预览必须在导入应用前明确设置临时 `DATABASE_URL`、`SQLITE_DB_PATH`、`INSTANCE_DIR` 和存储路径。不要只依赖 PowerShell 清空 `DATABASE_URL`，以免变量被移除后重新从 `.env` 读取。

## 7. 重跑本轮回归

在仓库根目录的 Git Bash 执行。需要已有 `.venv-audit`；浏览器测试会使用已安装的 Chrome/Edge，也支持通过 `UI_TEST_BROWSER` 指定 Chromium 可执行文件。没有浏览器时会跳过，接手者必须检查是否出现 skipped。

```bash
".venv-audit/Scripts/python.exe" - <<'PY'
import os
import tempfile
import unittest
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix='swu-ui-regression-'))
os.environ.update({
    'DATABASE_URL': 'sqlite:///' + (root / 'tests.db').as_posix(),
    'SQLITE_DB_PATH': str(root / 'tests.db'),
    'INSTANCE_DIR': str(root),
    'UPLOAD_FOLDER': str(root / 'uploads'),
    'AUTOMATION_UPLOAD_DIR': str(root / 'automation'),
    'AUTO_REVIEW_UPLOAD_DIR': str(root / 'auto-review'),
    'AUTO_REVIEW_REPORT_DIR': str(root / 'reports'),
    'EXPORT_DIR': str(root / 'exports'),
    'STORAGE_CLEANUP_ENABLED': '0',
    'DEEPSEEK_API_KEY': '',
    'DEEPSEEK_BASE_URL': 'http://127.0.0.1:9',
    'FLASK_ENV': 'development',
    'SECRET_KEY': 'ui-regression-isolated',
})
patterns = [
    'test_ui*.py', 'test_unified_shell.py', 'test_design_system_components.py',
    'test_login_template.py', 'test_public_routes.py', 'test_workspace*.py',
    'test_activity_center.py', 'test_people_responsive_contract.py',
    'test_course_page_header_migration.py', 'test_statistics_design_migration.py',
    'test_campus_theme_browser.py',
]
suite = unittest.TestSuite()
for pattern in patterns:
    suite.addTests(unittest.defaultTestLoader.discover('tests', pattern=pattern))
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(not result.wasSuccessful())
PY
```

接手建议：先阅读实现提交和本交接，重跑回归，再使用合成数据补人工验收。用户已确认最终配色，不必重新开启整套风格讨论。
