# 西大听课工作台统一重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留现有 Flask 路由和业务语义的前提下，为信息员、管理员和超级管理员建立墨黑朱红统一工作台，消除内部链接新开标签页，并重构高频填报、审核和管理流程。

**Architecture:** 继续使用 Flask、Jinja2、Bootstrap 5.3、Bootstrap Icons、jQuery 和原生 JavaScript。新增纯表示层 `app/ui` 模块、统一 `base.html` 壳层、共享 Jinja 局部模板和按职责拆分的静态脚本；高频页面采用当前页抽屉、主从布局和可恢复筛选状态，不引入 SPA 或新构建链。

**Tech Stack:** Python 3.10、Flask 2.3.3、Flask-SQLAlchemy 3.0.5、Jinja2、Bootstrap 5.3、Bootstrap Icons、jQuery、unittest、Playwright 浏览器验收。

---

## 执行分派与依赖

用户要求实际编码和运行检查由主会话创建并监控 Codex 会话任务，模型使用 5.6 Luna，推理档为 `max`。设计规格和本计划由主会话维护。

按以下顺序分派：

1. 会话任务 A 执行 Task 1 至 Task 5，建立统一基础。其他执行任务等待 A 完成并通过主会话审查。
2. 会话任务 B 执行 Task 6，改造信息员流程。
3. 会话任务 C 执行 Task 7，改造管理员和超级管理员高频流程。B 与 C 可以在 A 合入后并行，但必须使用隔离工作树。
4. 主会话审查并整合 B、C，解决 `base.html`、`style.css` 和公共脚本冲突。
5. 会话任务 D 执行 Task 8 和 Task 9，完成全站收口与验收。

每个会话任务必须先阅读：

- `docs/superpowers/specs/2026-08-10-unified-workspace-redesign.md`
- 本实施计划中被分派的任务
- `C:/Users/yoouzico/.codex/skills/test-driven-development/SKILL.md`
- `C:/Users/yoouzico/.codex/skills/design-taste-frontend/SKILL.md`
- `C:/Users/yoouzico/.codex/skills/verification-before-completion/SKILL.md`

## 文件职责总览

新增文件：

- `app/ui/__init__.py`：表示层包入口。
- `app/ui/navigation.py`：角色和权限驱动的导航构建器。
- `app/ui/workspace.py`：工作台数据快照、查询和视图模型组装。
- `app/templates/partials/_sidebar.html`：桌面端和移动端导航内容。
- `app/templates/partials/_topbar.html`：面包屑、搜索入口和用户区。
- `app/templates/partials/_flash_messages.html`：统一消息提示。
- `app/templates/partials/_page_header.html`：页面标题宏。
- `app/templates/partials/_empty_state.html`：空状态宏。
- `app/templates/partials/_confirm_dialog.html`：统一确认对话框。
- `app/templates/main/workspace.html`：三种角色共用工作台。
- `app/static/js/app-shell.js`：侧栏、移动端菜单、抽屉和对话框基础行为。
- `app/static/js/activity-center.js`：信息员业务中心交互。
- `app/static/js/review-queue.js`：审核队列上下文恢复和当前页导航。
- `tests/test_workspace_navigation.py`：纯导航构建器测试。
- `tests/test_workspace_routes.py`：工作台、登录和兼容重定向测试。
- `tests/test_unified_shell.py`：模板壳层和新标签页回归测试。
- `tests/test_activity_center.py`：信息员业务中心路由和模板测试。
- `tests/test_review_queue_template.py`：审核队列交互契约测试。

重点修改文件：

- `app/app.py`：注入当前用户、导航和当前端点。
- `app/blueprints/auth.py`：登录后统一跳转。
- `app/blueprints/main.py`：统一工作台入口。
- `app/blueprints/user.py`：信息员业务中心数据组装和兼容入口。
- `app/blueprints/admin.py`：旧仪表板兼容重定向和详情片段响应。
- `app/templates/base.html`：唯一应用壳层。
- `app/static/css/style.css`：设计令牌和共享组件。
- 高频业务模板：信息员填报与记录、审核队列与完整审核、人员和系统设置页面。

### Task 1: 导航构建器

**Files:**
- Create: `app/ui/__init__.py`
- Create: `app/ui/navigation.py`
- Create: `tests/test_workspace_navigation.py`

- [ ] **Step 1: 编写导航构建器失败测试**

```python
from types import SimpleNamespace
import unittest

from app.ui.navigation import build_navigation


class WorkspaceNavigationTest(unittest.TestCase):
    def user(self, role):
        return SimpleNamespace(id=1, role=role)

    def labels(self, groups):
        return [item['label'] for group in groups for item in group['items']]

    def test_information_officer_only_sees_personal_modules(self):
        groups = build_navigation(self.user('信息员'), 'main.index')
        labels = self.labels(groups)
        self.assertIn('今日工作', labels)
        self.assertIn('听课与填报', labels)
        self.assertNotIn('表单审核', labels)
        self.assertNotIn('系统设置', labels)

    def test_manager_modules_follow_explicit_permissions(self):
        groups = build_navigation(
            self.user('管理员'),
            'admin.review_forms',
            review_permission='审表_部门',
            manage_permission='管理部门',
        )
        labels = self.labels(groups)
        self.assertIn('表单审核', labels)
        self.assertIn('人员与部门', labels)
        active = [item for group in groups for item in group['items'] if item['active']]
        self.assertEqual([item['label'] for item in active], ['表单审核'])

    def test_super_admin_sees_global_modules(self):
        groups = build_navigation(self.user('超级管理员'), 'admin.system_management')
        labels = self.labels(groups)
        self.assertIn('系统设置', labels)
        self.assertIn('统计与导出', labels)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_workspace_navigation -v`

Expected: `ModuleNotFoundError: No module named 'app.ui'`。

- [ ] **Step 3: 实现纯导航构建器**

```python
from copy import deepcopy


NAVIGATION = (
    {
        'label': '工作',
        'items': (
            {'label': '今日工作', 'endpoint': 'main.index', 'icon': 'bi-house-door', 'roles': ('信息员', '管理员', '超级管理员'), 'active_endpoints': ('main.index',)},
            {'label': '听课与填报', 'endpoint': 'user.listening_registration', 'icon': 'bi-journal-text', 'roles': ('信息员', '管理员', '超级管理员'), 'active_endpoints': ('user.listening_registration', 'user.my_forms', 'user.submit_form', 'user.edit_form', 'user.view_form')},
            {'label': '表单审核', 'endpoint': 'admin.review_forms', 'icon': 'bi-clipboard-check', 'roles': ('管理员', '超级管理员'), 'permission': 'review', 'active_endpoints': ('admin.review_forms', 'admin.review_form_page')},
        ),
    },
    {
        'label': '管理',
        'items': (
            {'label': '人员与部门', 'endpoint': 'admin.manage_departments', 'icon': 'bi-people', 'roles': ('管理员', '超级管理员'), 'permission': 'manage', 'active_endpoints': ('admin.manage_departments', 'admin.manage_groups', 'admin.view_managed_user')},
            {'label': '课程与登记', 'endpoint': 'admin.course_feedback_management', 'icon': 'bi-calendar-week', 'roles': ('管理员', '超级管理员'), 'active_endpoints': ('admin.course_feedback_management', 'admin.registration_statistics')},
            {'label': '统计与导出', 'endpoint': 'admin.statistics', 'icon': 'bi-bar-chart', 'roles': ('管理员', '超级管理员'), 'active_endpoints': ('admin.statistics', 'admin.review_assessment_stats', 'admin.submission_count_stats', 'admin.department_monthly_assessment_stats')},
            {'label': '系统设置', 'endpoint': 'admin.system_management', 'icon': 'bi-sliders', 'roles': ('超级管理员',), 'active_endpoints': ('admin.system_management', 'admin.auto_review_page', 'admin.assessment_exemption_settings')},
        ),
    },
)


def build_navigation(user, endpoint='', review_permission=None, manage_permission=None):
    groups = []
    for group_definition in NAVIGATION:
        items = []
        for definition in group_definition['items']:
            if user.role not in definition['roles']:
                continue
            requirement = definition.get('permission')
            if user.role != '超级管理员' and requirement == 'review' and not review_permission:
                continue
            if user.role != '超级管理员' and requirement == 'manage' and not manage_permission:
                continue
            item = deepcopy(definition)
            item['active'] = endpoint in item.pop('active_endpoints', (item['endpoint'],))
            items.append(item)
        if items:
            groups.append({'label': group_definition['label'], 'items': items})
    return groups
```

Create `app/ui/__init__.py` as an empty package marker.

- [ ] **Step 4: 运行测试并确认通过**

Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_workspace_navigation -v`

Expected: `Ran 3 tests` and `OK`。

- [ ] **Step 5: 提交导航构建器**

```powershell
git add app/ui tests/test_workspace_navigation.py
git commit -m "feat: add role-aware workspace navigation"
```

### Task 2: 工作台视图模型

**Files:**
- Create: `app/ui/workspace.py`
- Modify: `tests/test_workspace_navigation.py`

- [ ] **Step 1: 编写角色工作台失败测试**

Append to `tests/test_workspace_navigation.py`:

```python
from app.ui.workspace import WorkspaceSnapshot, build_workspace


class WorkspaceViewModelTest(unittest.TestCase):
    def test_information_officer_gets_personal_primary_action(self):
        user = SimpleNamespace(id=2, name='周雨', role='信息员')
        model = build_workspace(user, WorkspaceSnapshot(total_forms=6, reservation_count=2, draft_saved=True))
        self.assertEqual(model['primary_action']['endpoint'], 'user.submit_form')
        self.assertEqual(model['metrics'][0]['value'], 6)
        self.assertEqual(model['tasks'][0]['label'], '继续未完成的听课表')

    def test_manager_gets_review_queue_action(self):
        user = SimpleNamespace(id=3, name='林老师', role='管理员')
        model = build_workspace(user, WorkspaceSnapshot(pending_forms=7, department_users=18, department_forms=38))
        self.assertEqual(model['primary_action']['endpoint'], 'admin.review_forms')
        self.assertEqual(model['metrics'][0]['value'], 7)

    def test_super_admin_gets_global_system_task(self):
        user = SimpleNamespace(id=4, name='林老师', role='超级管理员')
        model = build_workspace(user, WorkspaceSnapshot(pending_forms=7, total_users=52, total_forms=118, failed_jobs=1))
        self.assertEqual(model['tasks'][0]['endpoint'], 'admin.system_management')
        self.assertIn('1', model['tasks'][0]['description'])
```

- [ ] **Step 2: 运行测试并确认缺少工作台模块**

Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_workspace_navigation.WorkspaceViewModelTest -v`

Expected: import failure for `app.ui.workspace`。

- [ ] **Step 3: 实现不可变快照和角色视图模型**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceSnapshot:
    total_forms: int = 0
    pending_forms: int = 0
    reservation_count: int = 0
    department_users: int = 0
    department_forms: int = 0
    total_users: int = 0
    failed_jobs: int = 0
    draft_saved: bool = False


def build_workspace(user, snapshot):
    if user.role == '信息员':
        tasks = []
        if snapshot.draft_saved:
            tasks.append({
                'label': '继续未完成的听课表',
                'description': '草稿已经自动保存，可以从上次位置继续。',
                'endpoint': 'user.submit_form',
            })
        return {
            'role': user.role,
            'role_slug': 'information-officer',
            'title': f'你好，{user.name}',
            'summary': '继续今天的听课安排，或查看已经提交的记录。',
            'primary_action': {'label': '填写听课表', 'endpoint': 'user.submit_form'},
            'metrics': [
                {'label': '我的表单', 'value': snapshot.total_forms},
                {'label': '我的预约', 'value': snapshot.reservation_count},
            ],
            'tasks': tasks,
            'quick_actions': [
                {'label': '听课登记', 'endpoint': 'user.listening_registration'},
                {'label': '查看记录', 'endpoint': 'user.my_forms'},
            ],
        }

    if user.role == '管理员':
        return {
            'role': user.role,
            'role_slug': 'manager',
            'title': f'上午好，{user.name}',
            'summary': f'先处理 {snapshot.pending_forms} 份待审核表单，再查看部门提交情况。',
            'primary_action': {'label': '处理审核', 'endpoint': 'admin.review_forms'},
            'metrics': [
                {'label': '待审核', 'value': snapshot.pending_forms},
                {'label': '部门成员', 'value': snapshot.department_users},
                {'label': '部门表单', 'value': snapshot.department_forms},
            ],
            'tasks': [],
            'quick_actions': [
                {'label': '人员与部门', 'endpoint': 'admin.manage_departments'},
                {'label': '统计与导出', 'endpoint': 'admin.statistics'},
            ],
        }

    tasks = []
    if snapshot.failed_jobs:
        tasks.append({
            'label': '检查系统任务',
            'description': f'{snapshot.failed_jobs} 个导入或自动审核任务需要处理。',
            'endpoint': 'admin.system_management',
        })
    return {
        'role': user.role,
        'role_slug': 'super-admin',
        'title': f'上午好，{user.name}',
        'summary': f'全局共有 {snapshot.pending_forms} 份表单等待处理。',
        'primary_action': {'label': '处理审核', 'endpoint': 'admin.review_forms'},
        'metrics': [
            {'label': '待审核', 'value': snapshot.pending_forms},
            {'label': '活跃成员', 'value': snapshot.total_users},
            {'label': '全部表单', 'value': snapshot.total_forms},
        ],
        'tasks': tasks,
        'quick_actions': [
            {'label': '人员与部门', 'endpoint': 'admin.manage_departments'},
            {'label': '系统设置', 'endpoint': 'admin.system_management'},
            {'label': '统计与导出', 'endpoint': 'admin.statistics'},
        ],
    }
```

- [ ] **Step 4: 运行导航和视图模型测试**

Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_workspace_navigation -v`

Expected: all 6 tests pass。

- [ ] **Step 5: 提交工作台视图模型**

```powershell
git add app/ui/workspace.py tests/test_workspace_navigation.py
git commit -m "feat: define workspace view models"
```

### Task 3: 统一应用壳层

**Files:**
- Create: `tests/test_unified_shell.py`
- Create: `app/templates/partials/_sidebar.html`
- Create: `app/templates/partials/_topbar.html`
- Create: `app/templates/partials/_flash_messages.html`
- Create: `app/templates/partials/_page_header.html`
- Create: `app/templates/partials/_empty_state.html`
- Create: `app/templates/partials/_confirm_dialog.html`
- Create: `app/static/js/app-shell.js`
- Modify: `app/templates/base.html`
- Modify: `app/static/css/style.css`
- Modify: `app/app.py`

- [ ] **Step 1: 编写统一壳层失败测试**

```python
from pathlib import Path
import unittest


class UnifiedShellTest(unittest.TestCase):
    def test_base_uses_shared_shell_and_local_assets(self):
        template = Path('app/templates/base.html').read_text(encoding='utf-8')
        self.assertIn("partials/_sidebar.html", template)
        self.assertIn("partials/_topbar.html", template)
        self.assertIn("js/app-shell.js", template)
        self.assertNotIn("setAttribute('target', '_blank')", template)
        self.assertNotIn('code.jquery.com', template)

    def test_design_tokens_are_charcoal_and_brick(self):
        css = Path('app/static/css/style.css').read_text(encoding='utf-8')
        self.assertIn('--color-nav: #292d2b', css)
        self.assertIn('--color-accent: #9b493c', css)
        self.assertNotIn('#667eea', css)
        self.assertNotIn('linear-gradient', css)

    def test_shell_script_never_rewrites_link_targets(self):
        script = Path('app/static/js/app-shell.js').read_text(encoding='utf-8')
        self.assertNotIn('target', script)
        self.assertIn('data-shell-toggle', script)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行测试并确认共享壳层缺失**

Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_unified_shell -v`

Expected: three failures for missing includes, tokens, and script。

- [ ] **Step 3: 建立模板局部组件**

`app/templates/partials/_sidebar.html`:

```html
<aside class="app-sidebar" id="appSidebar" aria-label="主导航">
    <a class="app-brand" href="{{ url_for('main.index') }}">
        <span class="app-brand__name">西大听课工作台</span>
        <span class="app-brand__meta">教学信息中心</span>
    </a>
    <nav class="app-nav">
        {% for group in app_navigation %}
        <div class="app-nav__group">
            <div class="app-nav__label">{{ group.label }}</div>
            {% for item in group['items'] %}
            <a class="app-nav__link{% if item.active %} is-active{% endif %}" href="{{ url_for(item.endpoint) }}"{% if item.active %} aria-current="page"{% endif %}>
                <i class="bi {{ item.icon }}" aria-hidden="true"></i>
                <span>{{ item.label }}</span>
            </a>
            {% endfor %}
        </div>
        {% endfor %}
    </nav>
    <div class="app-sidebar__user">
        <strong>{{ current_user.name }}</strong>
        <span>{{ current_user.role }}</span>
    </div>
</aside>
```

`app/templates/partials/_topbar.html`:

```html
<header class="app-topbar">
    <button class="app-topbar__menu" type="button" data-shell-toggle aria-controls="appSidebar" aria-expanded="false" aria-label="打开导航">
        <i class="bi bi-list" aria-hidden="true"></i>
    </button>
    <div class="app-breadcrumb">{{ page_breadcrumb|default('今日工作') }}</div>
    <div class="app-topbar__actions">
        <form class="app-search" action="{{ url_for(app_search_endpoint) }}" method="get" role="search">
            <label class="visually-hidden" for="appSearch">搜索</label>
            <input class="form-control form-control-sm" id="appSearch" name="search" type="search" placeholder="搜索表单、人员或课程">
        </form>
        <a href="{{ url_for('user.profile') }}">{{ current_user.name }}</a>
        <a href="{{ url_for('auth.logout') }}">退出</a>
    </div>
</header>
```

`app/templates/partials/_flash_messages.html`:

```html
{% with messages = get_flashed_messages(with_categories=true) %}
{% if messages %}
<div class="app-messages" aria-live="polite">
    {% for category, message in messages %}
    <div class="app-message app-message--{{ category }}" role="status">
        <span>{{ message }}</span>
        <button type="button" data-bs-dismiss="alert" aria-label="关闭"><i class="bi bi-x-lg"></i></button>
    </div>
    {% endfor %}
</div>
{% endif %}
{% endwith %}
```

`app/templates/partials/_page_header.html`:

```html
{% macro page_header(title, description='', action_label='', action_href='') %}
<div class="page-header">
    <div><h1>{{ title }}</h1>{% if description %}<p>{{ description }}</p>{% endif %}</div>
    {% if action_label and action_href %}<a class="btn btn-primary" href="{{ action_href }}">{{ action_label }}</a>{% endif %}
</div>
{% endmacro %}
```

`app/templates/partials/_empty_state.html`:

```html
{% macro empty_state(title, description, action_label='', action_href='') %}
<div class="empty-state"><i class="bi bi-inbox" aria-hidden="true"></i><h2>{{ title }}</h2><p>{{ description }}</p>{% if action_label %}<a class="btn btn-primary" href="{{ action_href }}">{{ action_label }}</a>{% endif %}</div>
{% endmacro %}
```

`app/templates/partials/_confirm_dialog.html`:

```html
<div class="modal fade" id="appConfirmDialog" tabindex="-1" aria-labelledby="appConfirmTitle" aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered"><div class="modal-content"><div class="modal-header"><h2 class="modal-title fs-5" id="appConfirmTitle">确认操作</h2><button class="btn-close" type="button" data-bs-dismiss="modal" aria-label="关闭"></button></div><div class="modal-body" id="appConfirmMessage"></div><div class="modal-footer"><button class="btn btn-secondary" type="button" data-bs-dismiss="modal">取消</button><button class="btn btn-danger" type="button" data-confirm-submit>确认</button></div></div></div>
</div>
```

- [ ] **Step 4: 替换 `base.html` 为唯一壳层**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}西大听课工作台{% endblock %}</title>
    <link rel="icon" type="image/png" href="{{ url_for('static', filename='images/favicon.png') }}">
    <link href="{{ url_for('static', filename='vendor/bootstrap/css/bootstrap.min.css') }}" rel="stylesheet">
    <link href="{{ url_for('static', filename='vendor/bootstrap-icons/font/bootstrap-icons.css') }}" rel="stylesheet">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    {% block head %}{% endblock %}
</head>
<body class="{% block body_class %}{% endblock %}">
{% if current_user.is_authenticated %}<div class="app-shell">{% include 'partials/_sidebar.html' %}<div class="app-frame">{% include 'partials/_topbar.html' %}<main class="app-content" id="mainContent">
{% else %}<main class="public-shell">
{% endif %}
{% include 'partials/_flash_messages.html' %}
{% block content %}{% endblock %}
</main>
{% if current_user.is_authenticated %}</div><button class="app-backdrop" type="button" data-shell-close aria-label="关闭导航"></button></div>{% endif %}
{% include 'partials/_confirm_dialog.html' %}
<script src="{{ url_for('static', filename='vendor/jquery/jquery-3.6.0.min.js') }}"></script>
<script src="{{ url_for('static', filename='vendor/bootstrap/js/bootstrap.bundle.min.js') }}"></script>
<script src="{{ url_for('static', filename='js/app-shell.js') }}"></script>
{% block scripts %}{% endblock %}
</body>
</html>
```

- [ ] **Step 5: 实现墨黑朱红设计令牌和基础布局**

Replace `app/static/css/style.css` with this foundation, then retain only business-specific selectors that are not expressible through these shared classes:

```css
:root {
    --color-nav: #292d2b;
    --color-accent: #9b493c;
    --color-accent-strong: #78362e;
    --color-accent-soft: #f3e8e5;
    --color-background: #f5f3f1;
    --color-surface: #fffefd;
    --color-text: #231f1d;
    --color-text-muted: #746b66;
    --color-border: #e2dcd7;
    --sidebar-width: 248px;
    --control-radius: 8px;
    --surface-radius: 10px;
    --dialog-radius: 12px;
    --transition-fast: 160ms cubic-bezier(.2,.8,.2,1);
}
* { box-sizing: border-box; }
html { color-scheme: light; }
body { margin: 0; color: var(--color-text); background: var(--color-background); font-family: -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }
a { color: var(--color-accent-strong); }
.app-shell { min-height: 100dvh; display: grid; grid-template-columns: var(--sidebar-width) minmax(0,1fr); }
.app-sidebar { position: sticky; top: 0; height: 100dvh; display: flex; flex-direction: column; padding: 24px 16px; color: #f4efeb; background: var(--color-nav); }
.app-brand { display: block; padding: 0 10px 24px; color: inherit; text-decoration: none; }
.app-brand__name,.app-brand__meta { display: block; }
.app-brand__name { font-size: 1rem; font-weight: 800; }
.app-brand__meta { margin-top: 4px; color: #bdb6b0; font-size: .7rem; }
.app-nav__group + .app-nav__group { margin-top: 20px; }
.app-nav__label { padding: 0 10px 7px; color: #bdb6b0; font-size: .72rem; }
.app-nav__link { display: flex; gap: 10px; align-items: center; min-height: 40px; padding: 0 10px; border-radius: var(--control-radius); color: #ece7e3; text-decoration: none; transition: background var(--transition-fast),transform var(--transition-fast); }
.app-nav__link:hover,.app-nav__link.is-active { color: #fffaf6; background: rgb(255 255 255 / .11); }
.app-nav__link:active { transform: translateY(1px); }
.app-sidebar__user { margin-top: auto; padding: 18px 10px 0; border-top: 1px solid rgb(255 255 255 / .12); }
.app-sidebar__user strong,.app-sidebar__user span { display: block; }
.app-sidebar__user span { color: #bdb6b0; font-size: .75rem; }
.app-frame { min-width: 0; }
.app-topbar { position: sticky; top: 0; z-index: 20; min-height: 64px; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 0 28px; border-bottom: 1px solid var(--color-border); background: rgb(255 254 253 / .96); }
.app-topbar__menu { display: none; border: 0; background: transparent; }
.app-topbar__actions { display: flex; gap: 16px; align-items: center; }
.app-search { width: min(320px,34vw); }
.app-content { width: min(100%,1440px); margin: 0 auto; padding: 30px; }
.page-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; margin-bottom: 24px; }
.page-header h1 { margin: 0; font-size: clamp(1.55rem,2vw,2rem); letter-spacing: -.035em; }
.page-header p { max-width: 65ch; margin: 8px 0 0; color: var(--color-text-muted); }
.btn { border-radius: var(--control-radius); white-space: nowrap; }
.btn-primary { --bs-btn-bg: var(--color-accent); --bs-btn-border-color: var(--color-accent); --bs-btn-hover-bg: var(--color-accent-strong); --bs-btn-hover-border-color: var(--color-accent-strong); }
.card,.modal-content { border-color: var(--color-border); box-shadow: none; }
.card { border-radius: var(--surface-radius); }
.modal-content { border-radius: var(--dialog-radius); }
.form-control,.form-select { border-color: #cfc7c1; border-radius: var(--control-radius); }
.form-control:focus,.form-select:focus { border-color: var(--color-accent); box-shadow: 0 0 0 .2rem rgb(155 73 60 / .18); }
.app-messages { display: grid; gap: 8px; margin-bottom: 18px; }
.app-message { display: flex; justify-content: space-between; gap: 16px; padding: 12px 14px; border: 1px solid var(--color-border); border-left: 4px solid var(--color-accent); border-radius: var(--control-radius); background: var(--color-surface); }
.empty-state { max-width: 520px; margin: 40px auto; padding: 28px; text-align: center; }
.empty-state i { color: var(--color-accent); font-size: 2rem; }
.app-backdrop { display: none; }
@media (max-width: 1024px) { :root { --sidebar-width: 216px; } .app-content { padding: 24px; } }
@media (max-width: 768px) {
    .app-shell { display: block; }
    .app-sidebar { position: fixed; inset: 0 auto 0 0; z-index: 50; width: min(86vw,320px); transform: translateX(-100%); transition: transform var(--transition-fast); }
    .app-shell.is-nav-open .app-sidebar { transform: translateX(0); }
    .app-shell.is-nav-open .app-backdrop { position: fixed; inset: 0; z-index: 40; display: block; border: 0; background: rgb(35 31 29 / .42); }
    .app-topbar { padding: 0 16px; }
    .app-topbar__menu { display: inline-flex; }
    .app-search { display: none; }
    .app-content { padding: 20px 16px 88px; }
    .page-header { align-items: stretch; flex-direction: column; }
    .page-header .btn { width: 100%; }
}
@media (prefers-reduced-motion: reduce) { *,*::before,*::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; animation-iteration-count: 1 !important; } }
```

- [ ] **Step 6: 实现壳层脚本**

```javascript
document.addEventListener('DOMContentLoaded', () => {
    const shell = document.querySelector('.app-shell');
    if (!shell) return;
    const toggle = shell.querySelector('[data-shell-toggle]');
    const closeButtons = shell.querySelectorAll('[data-shell-close]');
    const setOpen = (open) => {
        shell.classList.toggle('is-nav-open', open);
        if (toggle) toggle.setAttribute('aria-expanded', String(open));
    };
    toggle?.addEventListener('click', () => setOpen(!shell.classList.contains('is-nav-open')));
    closeButtons.forEach((button) => button.addEventListener('click', () => setOpen(false)));
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') setOpen(false); });
});
```

- [ ] **Step 7: 在 `app/app.py` 注入导航**

Import `request`, `build_navigation`, `get_user_manage_permission`, and `get_user_review_permission`. Replace `inject_current_user` with a single resolution path that returns both user and navigation:

```python
@app.context_processor
def inject_app_context():
    user_id = session.get('user_id')
    user = User.query.get(user_id) if user_id else None
    if not user or not is_user_active(user):
        if user_id:
            session.clear()
        return {'current_user': AnonymousUser(), 'app_navigation': [], 'current_endpoint': request.endpoint or ''}
    user.is_authenticated = True
    review_permission = get_user_review_permission(user.id) if user.role == '管理员' else None
    manage_permission = get_user_manage_permission(user.id) if user.role == '管理员' else None
    return {
        'current_user': user,
        'app_navigation': build_navigation(user, request.endpoint or '', review_permission, manage_permission),
        'current_endpoint': request.endpoint or '',
        'app_search_endpoint': 'user.my_forms' if user.role == '信息员' else 'admin.view_forms',
    }
```

- [ ] **Step 8: 运行壳层和现有模板测试**

Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_unified_shell tests.test_login_template tests.test_user_profile_template -v`

Expected: all tests pass。

- [ ] **Step 9: 提交统一壳层**

```powershell
git add app/app.py app/templates/base.html app/templates/partials app/static/css/style.css app/static/js/app-shell.js tests/test_unified_shell.py
git commit -m "feat: add unified charcoal and brick app shell"
```

### Task 4: 统一工作台路由和模板

**Files:**
- Create: `tests/test_workspace_routes.py`
- Create: `app/templates/main/workspace.html`
- Modify: `app/ui/workspace.py`
- Modify: `app/blueprints/main.py`
- Modify: `app/blueprints/auth.py`
- Modify: `app/blueprints/admin.py`
- Modify: `app/static/css/style.css`

- [ ] **Step 1: 编写登录、工作台和兼容路由失败测试**

Create `tests/test_workspace_routes.py` with this setup before the assertions:

```python
import os
import tempfile
import unittest
from werkzeug.security import generate_password_hash

TEST_DB_DIR = tempfile.TemporaryDirectory()
os.environ['SQLITE_DB_PATH'] = os.path.join(TEST_DB_DIR.name, 'workspace_routes.db')
os.environ['INSTANCE_DIR'] = TEST_DB_DIR.name
os.environ['SECRET_KEY'] = 'test-secret-key'
os.environ['STORAGE_CLEANUP_ENABLED'] = '0'

from app.app import app
from app.models import User, db
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


class WorkspaceRouteTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        TEST_DB_DIR.cleanup()

    def setUp(self):
        configure_sqlite_database(app, db, os.environ['SQLITE_DB_PATH'])
        app.config.update(TESTING=True)
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self.information_officer = self.create_user('user001', 'U001', '周雨', '信息员')
        self.manager = self.create_user('manager', 'M001', '林老师', '管理员')
        self.super_admin = self.create_user('super', 'SA001', '超管测试', '超级管理员')
        db.session.commit()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.context.pop()

    def create_user(self, student_id, number, name, role):
        user = User(number=number, department='办公部', name=name, gender='-', grade='-', college='-', major='-', dormitory='-', phone='-', qq='-', student_id=student_id, password_hash=generate_password_hash('password'), role=role, group='一组', is_active=True)
        db.session.add(user)
        db.session.flush()
        return user
```

Add these methods inside `WorkspaceRouteTest`:

```python
    def login_session(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    def test_information_officer_root_renders_shared_workspace(self):
        self.login_session(self.information_officer)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-workspace-role="information-officer"', response.data)
        self.assertIn('填写听课表'.encode('utf-8'), response.data)

    def test_manager_root_renders_review_work(self):
        self.login_session(self.manager)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-workspace-role="manager"', response.data)
        self.assertIn('处理审核'.encode('utf-8'), response.data)

    def test_legacy_dashboards_redirect_to_root(self):
        self.login_session(self.super_admin)
        response = self.client.get('/admin/super_admin_dashboard')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')

    def test_login_redirects_every_role_to_root(self):
        response = self.client.post('/auth/login', data={'student_id': self.information_officer.student_id, 'password': 'password'})
        self.assertEqual(response.headers['Location'], '/')
```

- [ ] **Step 2: 运行路由测试并确认旧页面仍被渲染**

Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_workspace_routes -v`

Expected: assertions fail because role首页 and role仪表板 still exist。

- [ ] **Step 3: 为快照补充数据库加载函数**

In `app/ui/workspace.py`, import the required models and add `load_workspace_snapshot(user)`:

```python
def load_workspace_snapshot(user):
    from app.models import CourseRegistration, LectureForm, LectureFormDraft, User
    if user.role == '信息员':
        return WorkspaceSnapshot(
            total_forms=LectureForm.query.filter_by(listener_number=user.number).count(),
            reservation_count=CourseRegistration.query.filter_by(user_id=user.id).count(),
            draft_saved=LectureFormDraft.query.filter_by(user_id=user.id, draft_key='submit_form').first() is not None,
        )
    if user.role == '管理员':
        return WorkspaceSnapshot(
            pending_forms=LectureForm.query.join(User, LectureForm.listener_number == User.number).filter(User.department == user.department, LectureForm.status.in_(['待审核', '部门已审核'])).count(),
            department_users=User.query.filter_by(department=user.department, is_active=True).count(),
            department_forms=LectureForm.query.join(User, LectureForm.listener_number == User.number).filter(User.department == user.department).count(),
        )
    return WorkspaceSnapshot(
        pending_forms=LectureForm.query.filter(LectureForm.status.in_(['待审核', '部门已审核'])).count(),
        total_users=User.query.filter_by(is_active=True).count(),
        total_forms=LectureForm.query.count(),
    )
```

- [ ] **Step 4: 让 `main.index` 渲染统一工作台**

```python
@main_bp.route('/')
def index():
    user_id = session.get('user_id')
    if not user_id:
        return render_template('main/public_index.html')
    user = User.query.get(user_id)
    if not user or not is_user_active(user):
        session.clear()
        return render_template('main/public_index.html')
    snapshot = load_workspace_snapshot(user)
    workspace = build_workspace(user, snapshot)
    leave_prompt = get_pending_leave_makeup(user)
    return render_template('main/workspace.html', user=user, workspace=workspace, leave_prompt=leave_prompt)
```

- [ ] **Step 5: 创建角色中立工作台模板**

```html
{% extends "base.html" %}
{% from "partials/_page_header.html" import page_header %}
{% from "partials/_empty_state.html" import empty_state %}
{% block title %}今日工作 - 西大听课工作台{% endblock %}
{% block content %}
<div class="workspace" data-workspace-role="{{ workspace.role_slug }}">
    {{ page_header(workspace.title, workspace.summary, workspace.primary_action.label, url_for(workspace.primary_action.endpoint)) }}
    <section class="workspace-metrics" aria-label="工作指标">
        {% for metric in workspace.metrics %}<div class="workspace-metric"><strong>{{ metric.value }}</strong><span>{{ metric.label }}</span></div>{% endfor %}
    </section>
    <div class="workspace-grid">
        <section class="workspace-panel"><div class="workspace-panel__header"><h2>优先处理</h2></div>
            {% if workspace.tasks %}{% for task in workspace.tasks %}<article class="workspace-task"><div><h3>{{ task.label }}</h3><p>{{ task.description }}</p></div><a class="btn btn-outline-primary" href="{{ url_for(task.endpoint) }}">继续处理</a></article>{% endfor %}
            {% else %}{{ empty_state('当前没有待办', '可以从右侧快捷入口开始新的工作。') }}{% endif %}
        </section>
        <section class="workspace-panel"><div class="workspace-panel__header"><h2>快捷发起</h2></div><div class="workspace-launch-list">{% for action in workspace.quick_actions %}<a href="{{ url_for(action.endpoint) }}"><span>{{ action.label }}</span><i class="bi bi-chevron-right"></i></a>{% endfor %}</div></section>
    </div>
</div>
{% include 'main/_leave_reminder_modal.html' %}
{% endblock %}
```

Add `role_slug` to every `build_workspace` result with values `information-officer`, `manager`, and `super-admin`.

- [ ] **Step 6: 统一登录和旧仪表板重定向**

In `auth.login`, replace the role branch with `return redirect(url_for('main.index'))`.

In both dashboard routes, keep the decorators but return `redirect(url_for('main.index'))`.

- [ ] **Step 7: 添加工作台布局样式**

```css
.workspace-metrics { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); margin-bottom: 24px; border-block: 1px solid var(--color-border); }
.workspace-metric { padding: 20px 22px 20px 0; }
.workspace-metric + .workspace-metric { padding-left: 22px; border-left: 1px solid var(--color-border); }
.workspace-metric strong,.workspace-metric span { display: block; }
.workspace-metric strong { font-size: 2rem; font-variant-numeric: tabular-nums; letter-spacing: -.04em; }
.workspace-metric span { margin-top: 6px; color: var(--color-text-muted); }
.workspace-grid { display: grid; grid-template-columns: minmax(0,1.45fr) minmax(260px,.75fr); gap: 18px; }
.workspace-panel { overflow: hidden; border: 1px solid var(--color-border); border-radius: var(--surface-radius); background: var(--color-surface); }
.workspace-panel__header { padding: 16px 18px; border-bottom: 1px solid var(--color-border); }
.workspace-panel__header h2 { margin: 0; font-size: 1rem; }
.workspace-task { display: flex; justify-content: space-between; gap: 18px; align-items: center; padding: 16px 18px; }
.workspace-task + .workspace-task { border-top: 1px solid var(--color-border); }
.workspace-task h3 { margin: 0 0 4px; font-size: .95rem; }
.workspace-task p { margin: 0; color: var(--color-text-muted); font-size: .85rem; }
.workspace-launch-list a { display: flex; justify-content: space-between; padding: 14px 18px; color: var(--color-text); text-decoration: none; }
.workspace-launch-list a + a { border-top: 1px solid var(--color-border); }
@media (max-width: 768px) { .workspace-grid { grid-template-columns: 1fr; } .workspace-metrics { grid-template-columns: 1fr 1fr; } .workspace-metric:nth-child(n+3) { border-top: 1px solid var(--color-border); } }
```

- [ ] **Step 8: 运行工作台、壳层和认证测试**

Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_workspace_routes tests.test_workspace_navigation tests.test_unified_shell tests.test_login_template -v`

Expected: all tests pass。

- [ ] **Step 9: 提交统一工作台**

```powershell
git add app/ui/workspace.py app/blueprints/main.py app/blueprints/auth.py app/blueprints/admin.py app/templates/main/workspace.html app/static/css/style.css tests/test_workspace_routes.py
git commit -m "feat: route every role through unified workspace"
```

### Task 5: 公共入口和登录页

**Files:**
- Modify: `app/templates/main/public_index.html`
- Modify: `app/templates/auth/login.html`
- Modify: `tests/test_login_template.py`

- [ ] **Step 1: 编写公共页面视觉契约失败测试**

```python
def test_login_and_public_pages_use_public_shell_without_blue_purple_gradients(self):
    for path in ('app/templates/auth/login.html', 'app/templates/main/public_index.html'):
        template = Path(path).read_text(encoding='utf-8')
        self.assertIn('{% extends "base.html" %}', template)
        self.assertNotIn('linear-gradient', template)
        self.assertNotIn('#667eea', template)
        self.assertNotIn('<nav class="navbar', template)

def test_login_page_has_one_primary_submit_intent(self):
    template = Path('app/templates/auth/login.html').read_text(encoding='utf-8')
    self.assertEqual(template.count('type="submit"'), 1)
    self.assertIn('登录工作台', template)
```

- [ ] **Step 2: 运行测试并确认独立公共页失败**

Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_login_template -v`

Expected: public page fails the extends and gradient assertions。

- [ ] **Step 3: 将公共页和登录页改为排版驱动的墨黑朱红双栏布局**

Both templates extend `base.html` and write into `{% block content %}`. Use one `.auth-layout` with a `.auth-intro` and `.auth-panel`; retain the exact form fields `student_id` and `password`, one submit button labelled `登录工作台`, the existing password visibility control, and the existing login-help modal. Use this public page copy:

```html
<div class="auth-intro"><span class="auth-kicker">西南大学教学信息中心</span><h1>听课、审核与教学管理，在一个工作台完成。</h1><p>面向信息员、管理员和超级管理员的校内听课业务系统。</p></div>
```

Do not add generated imagery, gradients, statistics, version labels, or duplicate login buttons.

- [ ] **Step 4: 添加公共布局响应式样式**

```css
.public-shell { min-height: 100dvh; display: grid; place-items: center; padding: 32px; background: var(--color-background); }
.auth-layout { width: min(1080px,100%); display: grid; grid-template-columns: 1.15fr minmax(340px,.65fr); border: 1px solid var(--color-border); border-radius: var(--dialog-radius); overflow: hidden; background: var(--color-surface); }
.auth-intro { min-height: 560px; display: flex; flex-direction: column; justify-content: flex-end; padding: 54px; color: #f6f1ed; background: var(--color-nav); }
.auth-intro h1 { max-width: 13ch; margin: 12px 0 16px; font-size: clamp(2rem,4vw,3.5rem); line-height: 1.05; letter-spacing: -.045em; }
.auth-intro p { max-width: 36ch; color: #c6beb8; }
.auth-panel { display: flex; flex-direction: column; justify-content: center; padding: 42px; }
@media (max-width: 768px) { .public-shell { padding: 16px; } .auth-layout { grid-template-columns: 1fr; } .auth-intro { min-height: auto; padding: 30px; } .auth-panel { padding: 30px; } }
```

- [ ] **Step 5: 运行登录模板和路由测试**

Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_login_template tests.test_workspace_routes -v`

Expected: all tests pass。

- [ ] **Step 6: 提交公共入口**

```powershell
git add app/templates/main/public_index.html app/templates/auth/login.html app/static/css/style.css tests/test_login_template.py
git commit -m "feat: redesign public entry and login"
```

### Task 6: 信息员听课与填报中心

**Files:**
- Create: `tests/test_activity_center.py`
- Create: `app/templates/user/activity_center.html`
- Create: `app/templates/user/_registration_panel.html`
- Create: `app/templates/user/_records_panel.html`
- Create: `app/static/js/activity-center.js`
- Modify: `app/blueprints/user.py`
- Modify: `app/templates/user/lecture_form.html`
- Modify: `app/templates/user/lecture_reservation.html`
- Modify: `app/templates/user/profile.html`
- Modify: `app/templates/user/edit_profile.html`
- Modify: `app/templates/success.html`
- Modify: `app/static/css/style.css`

- [ ] **Step 1: 编写业务中心和表单结构失败测试**

Create `tests/test_activity_center.py` with this complete setup and assertions:

```python
import os
from pathlib import Path
import tempfile
import unittest
from werkzeug.security import generate_password_hash

TEST_DB_DIR = tempfile.TemporaryDirectory()
os.environ['SQLITE_DB_PATH'] = os.path.join(TEST_DB_DIR.name, 'activity_center.db')
os.environ['INSTANCE_DIR'] = TEST_DB_DIR.name
os.environ['SECRET_KEY'] = 'test-secret-key'
os.environ['STORAGE_CLEANUP_ENABLED'] = '0'

from app.app import app
from app.models import User, db
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


class ActivityCenterTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        TEST_DB_DIR.cleanup()

    def setUp(self):
        configure_sqlite_database(app, db, os.environ['SQLITE_DB_PATH'])
        app.config.update(TESTING=True)
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self.user = User(number='U001', department='办公部', name='周雨', gender='-', grade='-', college='-', major='-', dormitory='-', phone='-', qq='-', student_id='user001', password_hash=generate_password_hash('password'), role='信息员', group='一组', is_active=True)
        db.session.add(self.user)
        db.session.commit()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.context.pop()

    def login_session(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    def test_activity_center_supports_registration_and_records_tabs(self):
        self.login_session(self.user)
        response = self.client.get('/user/listening_registration?tab=records')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-activity-tab="registration"', response.data)
        self.assertIn(b'data-activity-tab="records"', response.data)
        self.assertIn(b'data-active-tab="records"', response.data)

    def test_my_forms_compatibility_route_redirects_to_records_tab(self):
        self.login_session(self.user)
        response = self.client.get('/user/my_forms')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/user/listening_registration?tab=records'))

    def test_lecture_form_has_section_navigation_and_save_status(self):
        template = Path('app/templates/user/lecture_form.html').read_text(encoding='utf-8')
        self.assertIn('class="form-section-nav"', template)
        self.assertIn('id="draftSaveStatus"', template)
        self.assertNotIn('bg-primary text-white', template)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行测试并确认当前页面分散**

Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_activity_center -v`

Expected: tab markers and compatibility redirect assertions fail。

- [ ] **Step 3: 提取 `my_forms` 的查询组装函数**

Move the existing form-group and reservation query body into `_build_activity_records(user, request_args)` and return `{'forms': form_groups, 'my_reservations': my_reservations}`. Do not change the query semantics, filters, edit permissions, delete permissions, or serialized values.

Modify routes:

```python
@user_bp.route('/listening_registration')
@login_required
def listening_registration():
    user = User.query.get(session['user_id'])
    active_tab = request.args.get('tab', 'registration')
    if active_tab not in {'registration', 'records'}:
        active_tab = 'registration'
    records = _build_activity_records(user, request.args)
    return render_template('user/activity_center.html', user=user, active_tab=active_tab, **records)

@user_bp.route('/my_forms')
@login_required
def my_forms():
    return redirect(url_for('user.listening_registration', tab='records', **request.args.to_dict(flat=True)))
```

- [ ] **Step 4: 建立同页标签模板**

`activity_center.html`:

```html
{% extends "base.html" %}
{% from "partials/_page_header.html" import page_header %}
{% block title %}听课与填报 - 西大听课工作台{% endblock %}
{% block content %}
{{ page_header('听课与填报', '登记听课安排，管理预约并查看已提交表单。', '填写听课表', url_for('user.submit_form')) }}
<div class="activity-center" data-active-tab="{{ active_tab }}">
    <div class="activity-tabs" role="tablist" aria-label="听课与填报">
        <button role="tab" data-activity-tab="registration" aria-selected="{{ 'true' if active_tab == 'registration' else 'false' }}">听课登记</button>
        <button role="tab" data-activity-tab="records" aria-selected="{{ 'true' if active_tab == 'records' else 'false' }}">预约与表单</button>
    </div>
    <section data-activity-panel="registration"{% if active_tab != 'registration' %} hidden{% endif %}>{% include 'user/_registration_panel.html' %}</section>
    <section data-activity-panel="records"{% if active_tab != 'records' %} hidden{% endif %}>{% include 'user/_records_panel.html' %}</section>
</div>
{% endblock %}
{% block scripts %}<script src="{{ url_for('static', filename='js/activity-center.js') }}"></script>{% endblock %}
```

Move the functional registration markup and JavaScript hooks from `app/templates/user/lecture_reservation.html` into `_registration_panel.html`. The current `/user/listening_registration` route renders `admin/course_feedback_management.html`; replacing that route with `activity_center.html` is intentional because the user-specific reservation template already targets the `/user/api/*` endpoints. Convert `lecture_reservation.html` into a compatibility template that extends `base.html` and includes `_registration_panel.html`. Move the current reservation and form list markup from `my_forms.html` into `_records_panel.html`. Preserve IDs used by existing JavaScript and every `/user/api/*` endpoint.

- [ ] **Step 5: 实现标签状态和 URL 同步**

```javascript
document.addEventListener('DOMContentLoaded', () => {
    const center = document.querySelector('.activity-center');
    if (!center) return;
    const activate = (name) => {
        center.dataset.activeTab = name;
        center.querySelectorAll('[data-activity-tab]').forEach((button) => button.setAttribute('aria-selected', String(button.dataset.activityTab === name)));
        center.querySelectorAll('[data-activity-panel]').forEach((panel) => { panel.hidden = panel.dataset.activityPanel !== name; });
        const url = new URL(window.location.href);
        url.searchParams.set('tab', name);
        window.history.replaceState({}, '', url);
    };
    center.querySelectorAll('[data-activity-tab]').forEach((button) => button.addEventListener('click', () => activate(button.dataset.activityTab)));
});
```

- [ ] **Step 6: 为长表单增加固定分区导航和持续保存状态**

Wrap the existing form sections with IDs `basic-information`, `course-information`, `classroom-review`, `feedback-information`, and `signature-information`. Add:

```html
<nav class="form-section-nav" aria-label="表单分区">
    <a href="#basic-information">基本信息</a><a href="#course-information">课程信息</a><a href="#classroom-review">课堂评价</a><a href="#feedback-information">反馈信息</a><a href="#signature-information">签名信息</a>
</nav>
<div class="draft-save-status" id="draftSaveStatus" role="status" aria-live="polite">尚未保存</div>
```

Update the existing draft JavaScript so each request sets text to `正在保存`, success sets `已保存 HH:mm`, and failure sets `保存失败，请检查网络后重试`. Keep the existing draft endpoint and payload unchanged.

- [ ] **Step 7: 统一信息员页面层级和提交结果**

Convert `profile.html`, `edit_profile.html`, and `success.html` to the shared page-header and section patterns. Remove page-specific blue headers, duplicated `.container mt-4`, oversized icon-only empty states, and gradient styles. The success page must include exactly these actions:

```html
<a class="btn btn-primary" href="{{ url_for('user.listening_registration', tab='records') }}">查看记录</a>
<a class="btn btn-outline-secondary" href="{{ url_for('user.submit_form') }}">继续填写</a>
```

- [ ] **Step 8: 添加业务中心和表单响应式样式**

```css
.activity-tabs { display: flex; gap: 4px; margin-bottom: 18px; border-bottom: 1px solid var(--color-border); }
.activity-tabs button { padding: 11px 14px; border: 0; border-bottom: 2px solid transparent; color: var(--color-text-muted); background: transparent; }
.activity-tabs button[aria-selected="true"] { border-bottom-color: var(--color-accent); color: var(--color-accent-strong); font-weight: 700; }
.lecture-form-layout { display: grid; grid-template-columns: 190px minmax(0,1fr); gap: 24px; }
.form-section-nav { position: sticky; top: 88px; align-self: start; display: grid; gap: 4px; }
.form-section-nav a { padding: 9px 10px; border-radius: var(--control-radius); color: var(--color-text-muted); text-decoration: none; }
.form-section-nav a:focus,.form-section-nav a:hover { color: var(--color-accent-strong); background: var(--color-accent-soft); }
.draft-save-status { color: var(--color-text-muted); font-size: .82rem; }
@media (max-width: 768px) { .lecture-form-layout { grid-template-columns: 1fr; } .form-section-nav { position: static; grid-auto-flow: column; overflow-x: auto; } }
```

- [ ] **Step 9: 运行信息员相关测试**

Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_activity_center tests.test_lecture_form_draft tests.test_user_profile_template -v`

Expected: all tests pass。

- [ ] **Step 10: 提交信息员业务中心**

```powershell
git add app/blueprints/user.py app/templates/user app/templates/success.html app/static/js/activity-center.js app/static/css/style.css tests/test_activity_center.py
git commit -m "feat: unify information officer activity flows"
```

### Task 7: 管理员审核与人员管理流程

**Files:**
- Create: `tests/test_review_queue_template.py`
- Create: `app/static/js/review-queue.js`
- Create: `app/templates/admin/_user_detail_panel.html`
- Modify: `app/blueprints/admin.py`
- Modify: `app/templates/admin/review_forms.html`
- Modify: `app/templates/admin/review_form.html`
- Modify: `app/templates/admin/view_forms.html`
- Modify: `app/templates/admin/manage_departments.html`
- Modify: `app/templates/admin/user_detail.html`
- Modify: `app/static/css/style.css`

- [ ] **Step 1: 编写审核队列和人员详情契约失败测试**

```python
from pathlib import Path
import unittest


class ReviewQueueTemplateTest(unittest.TestCase):
    def test_review_queue_stays_in_current_tab_and_restores_context(self):
        template = Path('app/templates/admin/review_forms.html').read_text(encoding='utf-8')
        self.assertNotIn("window.open(`/admin/review/form/${formId}`, '_blank')", template)
        self.assertIn("js/review-queue.js", template)
        script = Path('app/static/js/review-queue.js').read_text(encoding='utf-8')
        self.assertIn('reviewQueueState', script)
        self.assertIn('window.location.assign', script)

    def test_full_review_exposes_queue_return_action(self):
        template = Path('app/templates/admin/review_form.html').read_text(encoding='utf-8')
        self.assertIn('data-review-return', template)
        self.assertIn('id="draftSaveStatus"', template)

    def test_department_page_uses_detail_drawer_not_new_window(self):
        template = Path('app/templates/admin/manage_departments.html').read_text(encoding='utf-8')
        self.assertNotIn("window.open(`/admin/users/${userId}/view`, '_blank')", template)
        self.assertIn('id="userDetailDrawer"', template)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行测试并确认新标签页逻辑仍存在**

Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_review_queue_template -v`

Expected: failures for `window.open`, missing queue script, and missing drawer。

- [ ] **Step 3: 实现审核队列上下文脚本**

```javascript
const reviewQueueState = 'swu-tic:review-queue';

function currentQueueUrl() {
    return `${window.location.pathname}${window.location.search}`;
}

function saveQueueState() {
    sessionStorage.setItem(reviewQueueState, JSON.stringify({ url: currentQueueUrl(), scrollY: window.scrollY }));
}

function openFullReview(formId) {
    saveQueueState();
    const target = new URL(`/admin/review/form/${formId}`, window.location.origin);
    target.searchParams.set('return_to', currentQueueUrl());
    window.location.assign(target.toString());
}

document.addEventListener('DOMContentLoaded', () => {
    const saved = JSON.parse(sessionStorage.getItem(reviewQueueState) || 'null');
    if (saved && saved.url === currentQueueUrl()) requestAnimationFrame(() => window.scrollTo({ top: saved.scrollY, behavior: 'instant' }));
    document.querySelectorAll('[data-open-review]').forEach((button) => button.addEventListener('click', () => openFullReview(button.dataset.openReview)));
});
```

Reference it from `review_forms.html` and replace the current `window.open` call with `data-open-review="${formId}"` buttons.

- [ ] **Step 4: 为完整审核添加返回队列和可见草稿状态**

At the top of `review_form.html`, add:

```html
<div class="review-toolbar">
    <a class="btn btn-outline-secondary" data-review-return href="{{ request.args.get('return_to') or url_for('admin.review_forms') }}"><i class="bi bi-arrow-left"></i> 返回审核队列</a>
    <div class="draft-save-status" id="draftSaveStatus" role="status" aria-live="polite">尚未保存</div>
</div>
```

Reuse the same saving-state strings as Task 6. After successful submit or reject, redirect to the sanitized local `return_to` value when it starts with `/admin/review_forms`; otherwise use `url_for('admin.review_forms')`.

- [ ] **Step 5: 将人员详情改为当前页抽屉**

Extract the existing `user_detail.html` content body into `_user_detail_panel.html`. `user_detail.html` remains a full compatibility page that extends `base.html` and includes the partial.

In `view_managed_user`, add:

```python
if request.args.get('format') == 'fragment':
    return render_template('admin/_user_detail_panel.html', user=target_user, **detail_context)
return render_template('admin/user_detail.html', user=target_user, **detail_context)
```

Add this drawer to `manage_departments.html`:

```html
<div class="offcanvas offcanvas-end app-detail-drawer" tabindex="-1" id="userDetailDrawer" aria-labelledby="userDetailDrawerTitle"><div class="offcanvas-header"><h2 class="offcanvas-title fs-5" id="userDetailDrawerTitle">成员详情</h2><button type="button" class="btn-close" data-bs-dismiss="offcanvas" aria-label="关闭"></button></div><div class="offcanvas-body" id="userDetailDrawerBody" aria-live="polite"></div></div>
```

Replace `window.open` with a fetch to `/admin/users/${userId}/view?format=fragment`, render a skeleton before the request, show an inline retry button on failure, and open the Bootstrap offcanvas after content arrives.

- [ ] **Step 6: 统一查看表单的当前页行为**

Replace `window.open(`/admin/export_form/${formId}`, '_blank')` in `view_forms.html` with `window.location.assign(...)`. Keep file downloads as direct navigation because they return attachments. Remove any local script that assigns `_blank` targets.

- [ ] **Step 7: 应用主从布局和审核样式**

```css
.review-layout { display: grid; grid-template-columns: minmax(320px,.85fr) minmax(0,1.45fr); gap: 18px; }
.review-queue,.review-preview { min-width: 0; border: 1px solid var(--color-border); border-radius: var(--surface-radius); background: var(--color-surface); }
.review-toolbar { position: sticky; top: 64px; z-index: 10; display: flex; justify-content: space-between; align-items: center; padding: 12px 0; background: var(--color-background); }
.app-detail-drawer { width: min(720px,100vw); background: var(--color-surface); }
.skeleton-line { height: 12px; margin: 10px 0; border-radius: 6px; background: #e8e2de; }
@media (max-width: 1024px) { .review-layout { grid-template-columns: 1fr; } .review-preview { display: none; } }
```

- [ ] **Step 8: 运行管理端模板和相关路由测试**

Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_review_queue_template tests.test_review_form_draft tests.test_admin_auto_review_routes tests.test_admin_assessment_settings_routes -v`

Expected: all tests pass。

- [ ] **Step 9: 提交管理员流程改造**

```powershell
git add app/blueprints/admin.py app/templates/admin app/static/js/review-queue.js app/static/css/style.css tests/test_review_queue_template.py
git commit -m "feat: keep review and people management in context"
```

### Task 8: 全站模板收口与状态一致性

**Files:**
- Modify: `app/templates/admin/auto_review.html`
- Modify: `app/templates/admin/system_management.html`
- Modify: `app/templates/admin/admin_dashboard.html`
- Modify: `app/templates/admin/super_admin_dashboard.html`
- Modify: `app/templates/main/admin_index.html`
- Modify: `app/templates/main/manager_index.html`
- Modify: `app/templates/main/user_index.html`
- Modify: `app/templates/main/index.html`
- Modify: `app/templates/admin/assessment_exemption_settings.html`
- Modify: `app/templates/admin/auto_review_results.html`
- Modify: `app/templates/admin/course_feedback_management.html`
- Modify: `app/templates/admin/dashboard.html`
- Modify: `app/templates/admin/department_monthly_assessment_stats.html`
- Modify: `app/templates/admin/form_detail_page.html`
- Modify: `app/templates/admin/import_forms_excel.html`
- Modify: `app/templates/admin/import_users.html`
- Modify: `app/templates/admin/manage_groups.html`
- Modify: `app/templates/admin/registration_statistics.html`
- Modify: `app/templates/admin/review_assessment_stats.html`
- Modify: `app/templates/admin/statistics.html`
- Modify: `app/templates/admin/submission_count_stats.html`
- Create: `app/templates/admin/_settings_teaching.html`
- Create: `app/templates/admin/_settings_assessment.html`
- Create: `app/templates/admin/_settings_automation.html`
- Create: `app/templates/admin/_settings_imports.html`
- Create: `app/static/js/settings-center.js`
- Modify: `app/templates/auth/change_password.html`
- Modify: `app/templates/list.html`
- Modify: `app/templates/view.html`
- Modify: `tests/test_unified_shell.py`
- Modify: `app/static/css/style.css`

- [ ] **Step 1: 扩展完整页面壳层失败测试**

```python
FULL_PAGE_TEMPLATES = (
    'admin/auto_review.html',
    'admin/system_management.html',
    'admin/admin_dashboard.html',
    'admin/super_admin_dashboard.html',
    'main/admin_index.html',
    'main/manager_index.html',
    'main/user_index.html',
)

def test_every_legacy_full_page_uses_base_without_duplicate_document(self):
    for relative_path in FULL_PAGE_TEMPLATES:
        template = Path('app/templates', relative_path).read_text(encoding='utf-8')
        self.assertIn('{% extends "base.html" %}', template, relative_path)
        self.assertNotIn('<!DOCTYPE html>', template, relative_path)
        self.assertNotIn('<nav class="navbar', template, relative_path)
        self.assertNotIn("setAttribute('target', '_blank')", template, relative_path)

def test_templates_do_not_embed_forbidden_palette(self):
    forbidden = ('#667eea', '#764ba2', '#4facfe', '#00f2fe', '#43e97b', '#38f9d7')
    for path in Path('app/templates').rglob('*.html'):
        template = path.read_text(encoding='utf-8')
        for color in forbidden:
            self.assertNotIn(color, template, str(path))

def test_system_settings_center_declares_four_business_tabs(self):
    template = Path('app/templates/admin/system_management.html').read_text(encoding='utf-8')
    for name in ('teaching', 'assessment', 'automation', 'imports'):
        self.assertIn(f'data-settings-panel="{name}"', template)
    self.assertIn("js/settings-center.js", template)
```

- [ ] **Step 2: 运行测试并记录所有独立模板失败项**

Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_unified_shell -v`

Expected: failures identify every remaining standalone document and forbidden gradient color。

- [ ] **Step 3: 机械转换旧角色首页和仪表板**

Because routes now redirect to `main.index`, replace each legacy role home and dashboard template with a compatibility template that extends `base.html`, explains that the entry has moved, and provides one link to `main.index`. Do not retain duplicate dashboard JavaScript or navigation.

```html
{% extends "base.html" %}
{% block title %}入口已更新 - 西大听课工作台{% endblock %}
{% block content %}<div class="empty-state"><i class="bi bi-arrow-repeat"></i><h1>入口已更新</h1><p>请从统一工作台继续处理任务。</p><a class="btn btn-primary" href="{{ url_for('main.index') }}">返回今日工作</a></div>{% endblock %}
```

- [ ] **Step 4: 转换仍在使用的独立后台模板**

For `auto_review.html` and `system_management.html`, remove document, head, body, navbar, duplicated Bootstrap imports, and internal target-rewrite scripts. Add `{% extends "base.html" %}`, preserve every functional ID and existing business script in `{% block scripts %}`, and wrap the existing body content in `{% block content %}`. Replace blue contextual classes with neutral or accent classes; keep success, warning, and danger semantic colors.

- [ ] **Step 5: 将系统级配置收拢到同页标签中心**

Extract the existing teaching settings, assessment exemption, auto review, and import-tool bodies into the four `_settings_*.html` partials. Preserve their form IDs, API URLs, file inputs, and JavaScript function names. Render them from `system_management.html`:

```html
{% extends "base.html" %}
{% from "partials/_page_header.html" import page_header %}
{% block content %}
{{ page_header('系统设置', '集中管理教学周期、考核规则、自动审核和数据导入。') }}
<div class="settings-center" data-active-settings-tab="{{ active_tab }}">
    <div class="activity-tabs" role="tablist" aria-label="系统设置">
        {% for value, label in [('teaching','教学制度'),('assessment','考核豁免'),('automation','自动审核'),('imports','数据导入')] %}
        <button type="button" role="tab" data-settings-tab="{{ value }}" aria-selected="{{ 'true' if active_tab == value else 'false' }}">{{ label }}</button>
        {% endfor %}
    </div>
    <section data-settings-panel="teaching"{% if active_tab != 'teaching' %} hidden{% endif %}>{% include 'admin/_settings_teaching.html' %}</section>
    <section data-settings-panel="assessment"{% if active_tab != 'assessment' %} hidden{% endif %}>{% include 'admin/_settings_assessment.html' %}</section>
    <section data-settings-panel="automation"{% if active_tab != 'automation' %} hidden{% endif %}>{% include 'admin/_settings_automation.html' %}</section>
    <section data-settings-panel="imports"{% if active_tab != 'imports' %} hidden{% endif %}>{% include 'admin/_settings_imports.html' %}</section>
</div>
{% endblock %}
{% block scripts %}<script src="{{ url_for('static', filename='js/settings-center.js') }}"></script>{% endblock %}
```

Validate the query parameter and keep compatibility routes:

```python
@admin_bp.route('/system_management')
@role_required('超级管理员')
def system_management():
    active_tab = request.args.get('tab', 'teaching')
    if active_tab not in {'teaching', 'assessment', 'automation', 'imports'}:
        active_tab = 'teaching'
    available_departments = list(_get_accessible_department_users(session['user_id']).keys())
    return render_template(
        'admin/system_management.html',
        active_tab=active_tab,
        status=AutoReviewEngine().files_status(),
        available_departments=available_departments,
        is_super_admin=True,
    )

@admin_bp.route('/auto_review')
@role_required('超级管理员')
def auto_review_page():
    return redirect(url_for('admin.system_management', tab='automation'))

@admin_bp.route('/assessment-exemption-settings')
@role_required('管理员')
def assessment_exemption_settings():
    user = User.query.get(session['user_id'])
    if user.role == '超级管理员':
        return redirect(url_for('admin.system_management', tab='assessment'))
    if not _has_assessment_stats_access(user.id):
        flash('权限不足', 'error')
        return redirect(url_for('main.index'))
    return render_template(
        'admin/assessment_exemption_settings.html',
        available_departments=list(_get_accessible_department_users(user.id).keys()),
        is_super_admin=False,
    )
```

Create `settings-center.js` with explicit activation and initialization behavior:

```javascript
document.addEventListener('DOMContentLoaded', () => {
    const center = document.querySelector('.settings-center');
    if (!center) return;
    const activate = (name) => {
        center.dataset.activeSettingsTab = name;
        center.querySelectorAll('[data-settings-tab]').forEach((button) => button.setAttribute('aria-selected', String(button.dataset.settingsTab === name)));
        center.querySelectorAll('[data-settings-panel]').forEach((panel) => {
            const active = panel.dataset.settingsPanel === name;
            panel.hidden = !active;
            if (active && panel.dataset.initialized !== 'true') {
                panel.dataset.initialized = 'true';
                panel.dispatchEvent(new CustomEvent('settings:shown', { bubbles: true }));
            }
        });
        const url = new URL(window.location.href);
        url.searchParams.set('tab', name);
        window.history.replaceState({}, '', url);
    };
    center.querySelectorAll('[data-settings-tab]').forEach((button) => button.addEventListener('click', () => activate(button.dataset.settingsTab)));
    activate(center.dataset.activeSettingsTab || 'teaching');
});
```

- [ ] **Step 6: 统一剩余页面的标题、空状态和错误状态**

For each remaining full business template:

1. Use `_page_header.html` for the main title and single primary action.
2. Replace bare text empty states such as `暂无数据` with `_empty_state.html` and one relevant action or explanation.
3. Replace spinner-only loading blocks with `.skeleton-line` elements matching the result region.
4. Ensure fetch failures render an inline message and retry control in the failed region.
5. Remove template-local `.container mt-4`, gradient headers, pure-blue primary sections, and duplicate navigation.
6. Preserve all form field names, element IDs used by scripts, request URLs, table data fields, and permission-dependent controls.

- [ ] **Step 7: 运行全站模板契约和完整测试**

Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest tests.test_unified_shell -v`

Expected: all shell and palette checks pass。

Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest discover -v`

Expected: complete suite passes with zero failures and errors。

- [ ] **Step 8: 提交模板收口**

```powershell
git add app/templates app/static/css/style.css tests/test_unified_shell.py
git commit -m "refactor: unify remaining pages under app shell"
```

### Task 9: 编译、浏览器验收和视觉预检

**Files:**
- Create: `output/playwright/redesign/` screenshots during local verification, keep the directory ignored unless the repository already tracks visual evidence.
- Modify: `README.md` only if launch or role-entry instructions are no longer accurate.

- [ ] **Step 1: 运行完整自动化验证**

Run:

```powershell
..\SWU_TIC-main\.venv\Scripts\python.exe -m unittest discover -v
..\SWU_TIC-main\.venv\Scripts\python.exe -m compileall -q app tests tools
git diff --check
```

Expected: test suite reports `OK`; compileall and diff check exit 0 with no output。

- [ ] **Step 2: 启动本地应用并创建三类演示账号**

Run:

```powershell
..\SWU_TIC-main\.venv\Scripts\python.exe tools\init_user.py --create-demo-users --super-password Super@123456 --manager-password Manager@123456 --user-password User@123456
$env:FLASK_RUN_HOST='127.0.0.1'
$env:FLASK_RUN_PORT='5000'
..\SWU_TIC-main\.venv\Scripts\python.exe app\app.py
```

Expected: server responds at `http://127.0.0.1:5000` and all three accounts can authenticate。

- [ ] **Step 3: 对三种角色执行浏览器核心流程**

Use Playwright with separate browser contexts:

1. `user001 / User@123456`: login, root workspace, open activity center, switch tabs, open lecture form, verify section navigation and draft status, submit only when test data allows.
2. `manager / Manager@123456`: login, root workspace, open review queue, preserve filters, open full review in the same tab, return to the same queue position, open a department member in the drawer.
3. `super / Super@123456`: login, root workspace, open people, system settings, auto review and statistics; verify every page stays inside the shell.

For each role assert `page.context().pages.length === 1` after normal internal navigation. File downloads may create a browser download event but must not open a permanent product page.

- [ ] **Step 4: 截图检查四个视口**

Capture root workspace and one high-frequency page at widths 1440, 1024, 768, and 390. Inspect:

- no blue or purple primary UI;
- sidebar, topbar, content hierarchy and current navigation state;
- primary action contrast and one-line labels;
- mobile menu, full-screen drawer and bottom action spacing;
- no horizontal overflow except explicitly scrollable tables;
- no mixed corner-radius system, gradients, outer glows, duplicate CTA intent, or decorative empty cards;
- loading, empty, error, saved and success states remain readable.

- [ ] **Step 5: 运行 `design-taste-frontend` 预检的适用项目**

Record a short audit in the execution task response covering: design read, dial values, redesign mode, zero blue-purple gradients, one accent, shape consistency, button/form contrast, navigation height, reduced motion, mobile collapse, empty/loading/error states, Bootstrap Icons only, no generated imagery in task pages, and no visible em-dash characters in rewritten copy.

- [ ] **Step 6: 更新 README 并提交验证调整**

If README still describes separate role home pages, replace that section with the unified root entry and the role-aware sidebar. Then run the Step 1 commands again.

```powershell
git add README.md app tests tools
git commit -m "docs: document unified workspace entry"
```

If README already matches the new entry and no code adjustments were needed, do not create an empty commit.

## 主会话最终整合检查

主会话在接受任何会话任务结果前必须：

1. 阅读该任务的完整 diff，不只依赖任务总结。
2. 确认任务只改动计划范围内文件，且没有覆盖其他任务已合入的工作。
3. 复跑该任务的目标测试。
4. 合并 B、C 后复跑完整测试并检查 `style.css`、`base.html` 和共享脚本冲突。
5. 在所有执行会话完成后，独立运行 Task 9 的完整验证，不以子任务报告代替证据。
