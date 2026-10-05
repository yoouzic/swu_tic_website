# 自适应问答式听课助手实施计划

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 将现有“查询候选并填表”的听课助手改造成每轮只问一个问题、提供 A/B/C/D 回答、最多三至四轮即可缩小到具体节次并由用户确认的自适应问答向导。

**Architecture:** 保留现有 ListeningAssistantService、批次课表索引、备用来源、确认复核和 evidence 模型作为事实边界。新增纯合同和问答编排服务：它只根据当前已知事实和权威候选集合选择下一问，不复制最终确认逻辑。HTTP 层提供无状态的 start/answer 接口，客户端只保存展示状态；最终 candidate_id、来源批次、学期、人工覆盖仍交给现有确认接口重新验证。

**Tech Stack:** Flask blueprint、Flask-SQLAlchemy、Python dataclass、现有 unittest/pytest、Jinja、Bootstrap、原生 JavaScript、CSS、仓库 Playwright 验收脚本。

---

## 范围和固定决策

- 初始问题固定为“你还记得哪类信息？”。
- A/B/C 是当前候选动态生成的最多三个选项；D 打开当前字段对应的自定义输入。
- 默认最多三轮信息问题；仍有可解释歧义时允许第四轮；第四轮后停止追问并展示候选/手动填写。
- 单候选也必须保留“都不是”。
- 不能自动把候选当成事实；候选只在用户确认后进入现有表单。
- 不引入自由聊天大模型解析，不改教师/督导模板。
- 现有直接填写表单、候选 API、备用 API 和 confirm API 保持可用。

## 文件地图

| 文件 | 职责 |
|---|---|
| app/services/listening_assistant_guide_contracts.py | 问答状态、问题、选项和回答合同 |
| tests/test_listening_assistant_guide_contracts.py | 合同、预算和序列化测试 |
| app/services/listening_assistant.py | 增加可处理部分已知事实的候选过滤入口，复用已有匹配规则 |
| app/services/listening_assistant_guide.py | 选择下一问、生成前三选项、规范化 D 自定义回答、停止判断 |
| tests/test_listening_assistant_guide.py | 问答编排、问题预算、候选缩小和冲突测试 |
| app/blueprints/user/listening_assistant.py | 新增 guide/start 和 guide/answer 接口，保留旧接口 |
| tests/test_listening_assistant_routes.py | 新接口鉴权、字段白名单、状态边界和响应合同测试 |
| app/templates/user/lecture_form.html | 将助手面板从多输入框改为单问题视图，保留原表单字段 |
| app/static/js/listening-assistant.js | 问答状态机、A/B/C/D 交互、草稿恢复和确认桥接 |
| app/static/css/listening-assistant.css | 问题卡、选项按钮、历史摘要、D 输入、确认卡和移动端布局 |
| tests/test_listening_assistant_template.py | 新问答 DOM/static 合同测试 |
| app/blueprints/user/forms.py | 为草稿命名空间增加受限 guide 状态，保持旧 payload 兼容 |
| app/services/listening_assistant_evidence.py | 仅在需要时补充问答历史/问题计数的安全 provenance |
| tests/test_lecture_form_draft.py | 问答草稿保存、恢复、清理和未知字段拒绝 |
| tests/test_listening_assistant_evidence.py | 问答确认继续走服务端候选复核 |
| tools/qa/listening_assistant_browser_check.cjs | 将验收场景切换为 A/B/C/D 问答流程 |

---

## Task 1: 冻结问答合同和问题预算

**Files:**

- Create: app/services/listening_assistant_guide_contracts.py
- Create: tests/test_listening_assistant_guide_contracts.py

- [ ] **Step 1: 写失败的合同测试**

    from app.services.listening_assistant_guide_contracts import (
        GuidedAssistantState,
        GuidedOption,
        GuidedQuestion,
        MAX_GUIDED_QUESTIONS,
    )

    def test_initial_memory_question_has_three_memory_options_and_custom_answer():
        question = GuidedQuestion.initial_memory_question()
        assert question.kind == 'memory'
        assert [option.code for option in question.options] == ['A', 'B', 'C']
        assert question.allow_custom is True
        assert question.custom_label == 'D. 我自己填写'

    def test_guided_state_rejects_question_count_above_budget():
        state = GuidedAssistantState(
            known_facts={},
            candidate_ids=(),
            asked_question_kinds=(),
            question_count=MAX_GUIDED_QUESTIONS + 1,
            stage='question',
        )
        assert state.is_valid is False

    def test_option_public_dict_contains_no_private_schedule_fields():
        option = GuidedOption(
            code='A',
            label='张老师',
            value='张老师',
            candidate_count=2,
        )
        payload = option.to_public_dict()
        assert payload == {
            'code': 'A',
            'label': '张老师',
            'value': '张老师',
            'candidate_count': 2,
        }
        assert 'phone' not in payload
        assert 'student_signature1' not in payload

    def test_state_round_trip_is_bounded_and_json_safe():
        state = GuidedAssistantState(
            known_facts={'date': '2026-09-18', 'room': '8-309'},
            candidate_ids=('primary:1',),
            asked_question_kinds=('memory', 'date'),
            question_count=2,
            stage='question',
        )
        restored = GuidedAssistantState.from_public_dict(state.to_public_dict())
        assert restored == state

- [ ] **Step 2: 运行合同测试确认失败**

    & '.venv-audit\Scripts\python.exe' -m pytest tests/test_listening_assistant_guide_contracts.py -q

预期：FAIL，因为合同模块尚不存在。

- [ ] **Step 3: 实现最小合同**

实现冻结 dataclass：

    MAX_GUIDED_QUESTIONS = 4
    MAX_GUIDED_FACT_LENGTH = 120
    MAX_GUIDED_CANDIDATES = 20
    ALLOWED_GUIDED_STAGES = frozenset({'question', 'candidate', 'confirm', 'manual', 'done'})

    @dataclass(frozen=True)
    class GuidedOption:
        code: str
        label: str
        value: str
        candidate_count: int | None = None

        def to_public_dict(self) -> dict[str, object]:
            return {
                'code': self.code,
                'label': self.label,
                'value': self.value,
                'candidate_count': self.candidate_count,
            }

    @dataclass(frozen=True)
    class GuidedQuestion:
        kind: str
        prompt: str
        options: tuple[GuidedOption, ...]
        allow_custom: bool = True
        custom_label: str = 'D. 我自己填写'

        def to_public_dict(self) -> dict[str, object]:
            return {
                'kind': self.kind,
                'prompt': self.prompt,
                'options': [item.to_public_dict() for item in self.options],
                'allow_custom': self.allow_custom,
                'custom_label': self.custom_label,
            }

        @classmethod
        def initial_memory_question(cls):
            return cls(
                kind='memory',
                prompt='你还记得哪类信息？',
                options=(
                    GuidedOption('A', '听课日期', 'date'),
                    GuidedOption('B', '授课教师', 'teacher'),
                    GuidedOption('C', '教室', 'room'),
                ),
            )

    @dataclass(frozen=True)
    class GuidedAssistantState:
        known_facts: Mapping[str, str]
        candidate_ids: tuple[str, ...]
        asked_question_kinds: tuple[str, ...]
        question_count: int
        stage: str

        @property
        def is_valid(self) -> bool:
            return (
                self.stage in ALLOWED_GUIDED_STAGES
                and 0 <= self.question_count <= MAX_GUIDED_QUESTIONS
                and len(self.candidate_ids) <= MAX_GUIDED_CANDIDATES
            )

        def to_public_dict(self) -> dict[str, object]:
            return {
                'known_facts': dict(self.known_facts),
                'candidate_ids': list(self.candidate_ids),
                'asked_question_kinds': list(self.asked_question_kinds),
                'question_count': self.question_count,
                'stage': self.stage,
            }

        @classmethod
        def from_public_dict(cls, payload: Mapping[str, object]):
            return cls(
                known_facts=normalize_known_facts(payload['known_facts']),
                candidate_ids=normalize_candidate_ids(payload['candidate_ids']),
                asked_question_kinds=normalize_question_kinds(payload['asked_question_kinds']),
                question_count=normalize_question_count(payload['question_count']),
                stage=normalize_stage(payload['stage']),
            )

    @dataclass(frozen=True)
    class GuidedResult:
        state: GuidedAssistantState
        question: GuidedQuestion | None
        candidates: tuple[Candidate, ...]
        needs_confirmation: bool

        def to_public_dict(self) -> dict[str, object]:
            return {
                'state': self.state.to_public_dict(),
                'question': self.question.to_public_dict() if self.question else None,
                'candidates': [item.to_public_dict() for item in self.candidates],
                'needs_confirmation': self.needs_confirmation,
            }

    合同模块同时提供 normalize_known_facts、normalize_candidate_ids、
    normalize_question_kinds、normalize_question_count 和 normalize_stage。
    所有文本、问题类型、阶段、candidate_ids、问答次数和嵌套键都在合同层做长度/类型/白名单限制；合同层不读取 Flask request。

- [ ] **Step 4: 运行合同测试确认通过**

    & '.venv-audit\Scripts\python.exe' -m pytest tests/test_listening_assistant_guide_contracts.py -q

预期：所有合同测试 PASS。

- [ ] **Step 5: 提交**

    git add app/services/listening_assistant_guide_contracts.py tests/test_listening_assistant_guide_contracts.py
    git commit -m "feat: define adaptive listening guide contracts"

## Task 2: 增加部分事实过滤和问答编排服务

**Files:**

- Modify: app/services/listening_assistant.py
- Create: app/services/listening_assistant_guide.py
- Create: tests/test_listening_assistant_guide.py

- [ ] **Step 1: 写失败的问答服务测试**

使用注入的 ScheduleEntry provider，准备同一日期、两个教师、两个教室、三个节次和两个班级变体。测试服务接口：

    result = guide.start(known_facts={})
    assert result.question.kind == 'memory'
    assert [item.code for item in result.question.options] == ['A', 'B', 'C']

    result = guide.answer(
        result.state,
        question_kind='memory',
        option_code='A',
        custom_value=None,
    )
    assert result.question.kind == 'date'

    result = guide.answer(
        result.state,
        question_kind='date',
        option_code=None,
        custom_value='2026-09-18',
    )
    assert result.question.kind in {'teacher', 'room', 'period', 'candidate'}
    assert len(result.candidates) <= 3 or result.state.question_count <= 3

    result = guide.answer(
        result.state,
        question_kind=result.question.kind,
        option_code='A',
        custom_value=None,
    )
    assert result.state.question_count <= 4
    assert all(item.to_public_dict().get('phone') is None for item in result.candidates)

    for _ in range(3):
        if result.question is None:
            break
        result = guide.answer(
            result.state,
            question_kind=result.question.kind,
            option_code='D',
            custom_value='不确定',
        )
    assert result.state.question_count <= 4
    assert result.state.stage in {'candidate', 'confirm', 'manual', 'question'}

- [ ] **Step 2: 运行测试确认失败**

    & '.venv-audit\Scripts\python.exe' -m pytest tests/test_listening_assistant_guide.py -q

预期：FAIL，因为可处理部分事实的搜索入口和 guide 服务不存在。

- [ ] **Step 3: 在现有搜索服务中增加部分事实入口**

在 ListeningAssistantService 增加：

    def search_partial(
        self,
        known_facts: Mapping[str, object],
        *,
        semester: str | None = None,
        rejected_ids: Iterable[object] | object | None = None,
    ) -> ListeningAssistantSearchResult:
        """Filter only by supplied facts; never selects a source implicitly."""

实现要求：

- 继续通过当前 primary batch loader 取得 ScheduleEntry；
- 复用现有日期、地点、教师、节次、班级标准化和冲突判断；
- 未提供日期/教师/教室时允许返回候选集合，但限制最多 MAX_GUIDED_CANDIDATES；
- 不把 period mismatch 直接丢弃，保留 conflict；
- 不读取 legacy Course 聚合或历史备份；
- 结果排序必须稳定：period start、course title、teacher、candidate_id；
- 旧 search/search_by_teacher/search_backup 的行为和签名不改变。

- [ ] **Step 4: 实现问答编排**

在 listening_assistant_guide.py 中实现：

    class ListeningAssistantGuideService:
        def __init__(self, assistant_service: ListeningAssistantService):
            self._assistant_service = assistant_service

        def start(
            self,
            *,
            known_facts: Mapping[str, object] | None = None,
            semester: str | None = None,
        ) -> GuidedResult:
            return self._advance(
                known_facts=known_facts or {},
                state=None,
                semester=semester,
            )

        def answer(
            self,
            state: GuidedAssistantState,
            *,
            question_kind: str,
            option_code: str | None,
            custom_value: object | None,
            semester: str | None = None,
        ) -> GuidedResult:
            facts = apply_guided_answer(
                state=state,
                question_kind=question_kind,
                option_code=option_code,
                custom_value=custom_value,
            )
            return self._advance(
                known_facts=facts,
                state=state,
                semester=semester,
            )

编排规则：

- 空状态返回 memory 问题；
- A/B/C 选择映射到 date、teacher、room；
- D 只接受当前问题对应字段，不能一次写入任意多个事实；
- 每次 answer 都从 state.known_facts 重新跑 search_partial；
- apply_guided_answer 只返回规范化的 known_facts，不直接返回客户端候选；
- _advance 负责调用 search_partial、计算问题区分度、生成 GuidedResult；
- 用每个候选字段的基数计算区分度，选择能最大化候选分裂的未问字段；
- 当前候选为一个且无冲突时 stage=confirm；多个候选不超过三个时返回候选选项；
- 计数达到 4 且没有可解释的区分字段时 stage=manual 或 candidate；
- 返回的候选只来自服务端本次计算，不能直接采用 client candidate_ids；
- 不保存数据库会话；客户端状态只作为下一次请求的受限输入。

- [ ] **Step 5: 运行服务测试和旧搜索回归**

    & '.venv-audit\Scripts\python.exe' -m pytest tests/test_listening_assistant_guide.py tests/test_listening_assistant_search.py tests/test_listening_assistant_service.py -q

预期：新问答测试和旧搜索测试全部 PASS。

- [ ] **Step 6: 提交**

    git add app/services/listening_assistant.py app/services/listening_assistant_guide.py tests/test_listening_assistant_guide.py
    git commit -m "feat: add adaptive listening guide orchestration"

## Task 3: 暴露 start/answer API

**Files:**

- Modify: app/blueprints/user/listening_assistant.py
- Modify: app/blueprints/user/__init__.py only if the existing module import does not expose the routes
- Modify: tests/test_listening_assistant_routes.py

- [ ] **Step 1: 写失败的路由测试**

测试接口：

    POST /user/api/listening-assistant/guide/start
    body: {"known_facts": {}, "semester": "2026-2027-1"}

    POST /user/api/listening-assistant/guide/answer
    body: {
        "state": {
            "known_facts": {"date": "2026-09-18"},
            "candidate_ids": [],
            "asked_question_kinds": ["memory"],
            "question_count": 1,
            "stage": "question"
        },
        "question_kind": "date",
        "option_code": null,
        "custom_value": "2026-09-18",
        "semester": "2026-2027-1"
    }

断言：

- 匿名和 inactive 用户返回 JSON 401；
- 任意 active authenticated role 返回 200；
- start 返回 question、state、candidates、needs_confirmation；
- answer 重新计算结果，不接受客户端伪造 candidate snapshot；
- 未知 state 键、未知 question_kind、question_count > 4、超长 custom_value、同时提供 option_code 和 custom_value 均返回 400；
- payload 和响应不包含 phone、signature、credential、evaluation text；
- success/data/message envelope 与已有接口一致。

- [ ] **Step 2: 运行测试确认失败**

    & '.venv-audit\Scripts\python.exe' -m pytest tests/test_listening_assistant_routes.py -q

预期：新增 guide 路由测试 FAIL。

- [ ] **Step 3: 实现 JSON 路由**

沿用现有 _assistant_login_required、_json_object、错误 envelope 和服务构造方式：

    @user_bp.route('/api/listening-assistant/guide/start', methods=['POST'])
    @_assistant_login_required
    def listening_assistant_guide_start(user):
        payload = _json_object()
        result = _guide_service_for_request().start(
            known_facts=payload.get('known_facts') or {},
            semester=payload.get('semester'),
        )
        return _envelope(True, result.to_public_dict(), '')

    @user_bp.route('/api/listening-assistant/guide/answer', methods=['POST'])
    @_assistant_login_required
    def listening_assistant_guide_answer(user):
        payload = _json_object()
        state = GuidedAssistantState.from_public_dict(payload['state'])
        result = _guide_service_for_request().answer(
            state,
            question_kind=payload['question_kind'],
            option_code=payload.get('option_code'),
            custom_value=payload.get('custom_value'),
            semester=payload.get('semester'),
        )
        return _envelope(True, result.to_public_dict(), '')

路由只负责 JSON 解析、白名单校验、调用 guide 服务和安全序列化；不得在路由中复制候选匹配或来源选择逻辑。_guide_service_for_request 负责用当前学期构造 primary-only 的 guide service。guide answer 每次传入当前学期并重新查询 primary source。confirm 仍使用现有 endpoint。

- [ ] **Step 4: 运行路由测试和原有接口测试**

    & '.venv-audit\Scripts\python.exe' -m pytest tests/test_listening_assistant_routes.py tests/test_listening_assistant_evidence.py -q

预期：全部 PASS。

- [ ] **Step 5: 提交**

    git add app/blueprints/user/listening_assistant.py app/blueprints/user/__init__.py tests/test_listening_assistant_routes.py
    git commit -m "feat: expose adaptive listening guide APIs"

## Task 4: 将表单前助手改为单问题 A/B/C/D 视图

**Files:**

- Modify: app/templates/user/lecture_form.html
- Modify: app/static/js/listening-assistant.js
- Modify: app/static/css/listening-assistant.css
- Modify: tests/test_listening_assistant_template.py

- [ ] **Step 1: 写失败的模板和 static contract 测试**

在 test_listening_assistant_template.py 增加断言：

    assert '你还记得哪类信息' in TEMPLATE
    assert 'data-assistant-question' in TEMPLATE
    assert 'data-assistant-option' in TEMPLATE
    assert 'data-assistant-custom' in TEMPLATE
    assert 'D. 我自己填写' in TEMPLATE
    assert 'data-assistant-history' in TEMPLATE
    assert 'data-assistant-progress' in TEMPLATE
    assert '/user/api/listening-assistant/guide/start' in ASSISTANT_JS
    assert '/user/api/listening-assistant/guide/answer' in ASSISTANT_JS
    assert 'window.alert' not in ASSISTANT_JS
    assert 'candidates[0]' not in ASSISTANT_JS

- [ ] **Step 2: 运行测试确认失败**

    & '.venv-audit\Scripts\python.exe' -m pytest tests/test_listening_assistant_template.py -q

预期：新的 question/history/guide endpoint hooks 不存在而 FAIL。

- [ ] **Step 3: 替换助手面板为 question view**

在现有 form 的 assistant section 中保留：

- assistant_payload 隐藏域；
- 原始 lecture_date、lecture_location、teacher_name、course_title、class_period 等字段；
- 手动填写和确认后的原表单流程。

将原先的并列 query-grid 改为以下语义 hooks：

    <div data-assistant-history aria-live="polite"></div>
    <div data-assistant-progress></div>
    <h3 data-assistant-question></h3>
    <div data-assistant-options role="group"></div>
    <div data-assistant-custom hidden>
        <label data-assistant-custom-label></label>
        <input data-assistant-custom-input>
        <button data-assistant-custom-submit>D. 提交自定义回答</button>
    </div>
    <button data-assistant-none>这些都不是</button>
    <button data-assistant-back>返回上一问</button>

候选确认视图继续显示课程、教师、教室、节次、来源和冲突；备用确认、room choice、period override 和 confirm button 不能删除。

- [ ] **Step 4: 实现浏览器问答状态机**

在 listening-assistant.js 中实现以下边界：

    const guideState = {
        state: null,
        question: null,
        history: [],
        requestId: 0,
        controller: null,
        customMode: false,
    };

实现 startGuide、answerOption、openCustomAnswer、submitCustomAnswer、goBack、openManual 和 renderGuide。每次回答：

- abort/ignore stale fetch；
- 把问题和回答写入仅用于展示的 history；
- 仅把 option_code 或当前字段 custom_value 发送给服务端；
- 返回结果中的 candidates/question/state 完整替换旧状态；
- 返回候选后显示候选 A/B/C/D，并保留“这些都不是”；
- 进入 confirm 前不填充正式表单；
- confirm 成功后复用现有 field_snapshot 和 assistant_payload；
- custom input 使用 textContent/属性写入，不能通过 innerHTML 拼接用户文本；
- 既有草稿保存/恢复桥接只保存受限 guide 状态，不保存任意嵌套字段。

- [ ] **Step 5: 加入可访问和移动样式**

保持 Bootstrap/paper 主题，要求：

- question option、D、自定义提交、返回和手动按钮最小 48px；
- 当前问题有明显 focus-visible；
- 历史答案与当前问题可区分；
- 320px 宽度不出现横向滚动；
- 选项文本和自定义文本 overflow-wrap:anywhere；
- 不新增外部 CDN。

- [ ] **Step 6: 运行 static/UI contract 测试**

    node --check app/static/js/listening-assistant.js
    & '.venv-audit\Scripts\python.exe' -m pytest tests/test_listening_assistant_template.py tests/test_activity_center.py tests/test_lecture_form_draft.py -q

预期：全部 PASS。

- [ ] **Step 7: 提交**

    git add app/templates/user/lecture_form.html app/static/js/listening-assistant.js app/static/css/listening-assistant.css tests/test_listening_assistant_template.py tests/test_lecture_form_draft.py
    git commit -m "feat: turn listening assistant into adaptive question flow"

## Task 5: 接入确认、证据和草稿的问答 provenance

**Files:**

- Modify: app/blueprints/user/forms.py
- Modify: app/services/listening_assistant_evidence.py
- Modify: tests/test_lecture_form_draft.py
- Modify: tests/test_listening_assistant_evidence.py

- [ ] **Step 1: 写失败的 provenance 测试**

覆盖：

- guide state 可以保存 history、question_count、known_facts 和 stage；
- assistant payload 只接受这些字段，未知嵌套键返回 400；
- 用户修改上一问后旧 candidate_ids、历史确认和 assistant-filled provenance 会被清空；
- guided candidate confirm 仍要求 fresh candidate/source/semester；
- 旧无 assistant payload 的直接提交完全不变；
- 证据只保存规范化问题历史和用户选择，不保存电话、签名、凭据或原始评价文本。

- [ ] **Step 2: 运行测试确认失败**

    & '.venv-audit\Scripts\python.exe' -m pytest tests/test_lecture_form_draft.py tests/test_listening_assistant_evidence.py -q

预期：新增 guide provenance 测试 FAIL。

- [ ] **Step 3: 扩展安全 draft namespace**

将 assistant draft whitelist 扩展为：

    assistant: {
        stage,
        guide_state: {
            known_facts,
            candidate_ids,
            asked_question_kinds,
            question_count,
            stage,
        },
        history: [
            {kind, answer_code, custom_value}
        ],
        source_kind,
        candidate_id,
        overrides,
        template_version,
    }

所有 list 长度、文本长度、问题类型、候选 ID、阶段和嵌套键都必须有限制。history 只保留展示所需的脱敏规范化内容。

- [ ] **Step 4: 确认路径保持服务端权威**

确认时只使用 candidate_id、source_kind、semester、source_batch_id、overrides 等既有安全字段；guide history 只作为 evidence provenance，不用来绕过 revalidate_selection。字段填充仍只发生在 blank fields，手动填写优先级不变。

- [ ] **Step 5: 运行 evidence、draft、transaction 回归**

    & '.venv-audit\Scripts\python.exe' -m pytest tests/test_lecture_form_draft.py tests/test_listening_assistant_evidence.py tests/test_submit_form_transaction.py tests/test_form_version_semantics.py -q

预期：全部 PASS。

- [ ] **Step 6: 提交**

    git add app/blueprints/user/forms.py app/services/listening_assistant_evidence.py tests/test_lecture_form_draft.py tests/test_listening_assistant_evidence.py
    git commit -m "feat: preserve guided question provenance"

## Task 6: 更新浏览器验收为问答式流程

**Files:**

- Modify: tools/qa/listening_assistant_browser_check.cjs

- [ ] **Step 1: 写验收 fixture 和场景**

使用 page.route 提供本地、非生产的 guide/start、guide/answer、candidate confirm fixtures。每个 fixture 验证请求方法、question_kind、option_code/custom_value、question_count、semester、candidate_id 和来源字段。

至少执行：

1. 初始“你还记得什么”显示 A/B/C/D；
2. 选择 A 日期后只显示一个日期问题；
3. 选择 B 教师后只显示教师问题；
4. D 自定义输入进入对应字段并安全显示文本；
5. 三轮内从候选集合进入具体节次确认；
6. 四轮预算和仍不确定后的手动路径；
7. 单候选也显示“都不是”；
8. 返回上一问会清空后续候选；
9. 备用来源 acknowledgement、room conflict 和 confirm 仍受门禁；
10. 键盘焦点、320px overflow、console/pageerror=0。

- [ ] **Step 2: 运行浏览器脚本**

    node --check tools/qa/listening_assistant_browser_check.cjs
    node tools/qa/listening_assistant_browser_check.cjs

无显式 loopback、账号、密码或 Chromium 时只能输出 NOT_RUN/BLOCKED；不得下载浏览器或启动生产服务。

- [ ] **Step 3: 提交**

    git add tools/qa/listening_assistant_browser_check.cjs
    git commit -m "test: verify adaptive listening guide flow"

## Task 7: 全量回归、schema 和范围审计

**Files:**

- No production files added beyond Tasks 1-6.

- [ ] **Step 1: 运行完整相关 Python suite**

    & '.venv-audit\Scripts\python.exe' -m pytest tests/test_activity_center.py tests/test_lecture_form_draft.py tests/test_submit_form_transaction.py tests/test_form_version_semantics.py tests/test_form_registration_bindings.py tests/test_schedule_snapshots.py tests/test_canonical_cutover.py tests/test_listening_assistant_service.py tests/test_listening_assistant_search.py tests/test_listening_assistant_schedule.py tests/test_listening_assistant_guide_contracts.py tests/test_listening_assistant_guide.py tests/test_listening_assistant_routes.py tests/test_listening_assistant_evidence.py tests/test_listening_assistant_template.py tests/test_navigation.py -q

预期：全部 PASS；只允许记录已有 SQLAlchemy deprecation warnings。

- [ ] **Step 2: 验证隔离 schema**

使用 LOCAL_DEBUG_MODE=1 和临时 SQLITE_DB_PATH：

    & '.venv-audit\Scripts\python.exe' -m flask --app app.app:app listening-assistant init-schema
    & '.venv-audit\Scripts\python.exe' -m flask --app app.app:app listening-assistant init-schema

使用 sqlite3 只读确认 schedule_import_batches、listening_assistant_schedule_entries 和 listening_assistant_evidence 存在。禁止对默认业务数据库运行课表导入。

- [ ] **Step 3: 运行静态和 Git 检查**

    node --check app/static/js/listening-assistant.js
    node --check tools/qa/listening_assistant_browser_check.cjs
    git diff --check
    git status --short

确认未修改用户未跟踪的 XLSX、旧计划和 output/。

- [ ] **Step 4: 最终范围审查**

确认：

- 问答服务没有复制或替换既有候选匹配规则；
- “都不是”、来源确认、冲突覆盖和手动填写仍可达；
- 信息员旧提交流程和教师/督导边界没有被扩大；
- 所有真实浏览器未执行项在最终报告中明确标记。

## 计划自审

- 规格覆盖：初始 A/B/C/D、动态区分、三至四轮预算、候选确认、服务端复核、草稿 provenance、旧表单兼容和浏览器验收分别由 Tasks 1-7 覆盖。
- 占位符扫描：没有 TBD、TODO、未命名的“适当处理”或依赖后续补充的步骤。
- 类型一致性：GuidedAssistantState、GuidedQuestion、GuidedOption、GuidedResult 在 Task 1 定义，Task 2-6 使用相同字段名；guide/start 和 guide/answer 的请求字段在路由、前端和测试中保持一致。
- 范围边界：本计划只重做问答入口和编排，保留现有候选/确认事实边界；教师/督导模板仍另立计划。

