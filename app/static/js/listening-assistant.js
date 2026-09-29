(function () {
    'use strict';

    const GUIDE_ENDPOINTS = Object.freeze({
        start: '/user/api/listening-assistant/guide/start',
        answer: '/user/api/listening-assistant/guide/answer',
        confirm: '/user/api/listening-assistant/confirm',
    });
    const TEMPLATE_VERSION = 'task4-v1';
    const MAX_QUESTIONS = 4;
    const FACT_KINDS = Object.freeze(['date', 'teacher', 'room', 'period', 'student_grade_class']);
    const SOURCE_LABELS = Object.freeze({
        primary: '当前权威课表',
        backup: '备用课表线索 · 需核对',
    });
    const FACT_LABELS = Object.freeze({
        date: '听课日期',
        teacher: '授课教师',
        room: '教室',
        period: '节次',
        student_grade_class: '听课班级',
        memory: '记忆类型',
        candidate: '课程候选',
    });
    const CONFLICT_LABELS = Object.freeze({
        period_mismatch: '查询节次与课表节次不一致，需核对。',
        venue_period_needs_confirmation: '场地节次需要人工核对。',
        date_needs_confirmation: '日期由星期或周次推断，需人工核对。',
    });
    const ASSISTANT_FILLED_FIELD_WHITELIST = Object.freeze([
        'lecture_date',
        'lecture_location',
        'teacher_name',
        'teacher_college',
        'course_title',
        'student_grade_class',
    ]);
    const ASSISTANT_FILLED_GROUP_WHITELIST = Object.freeze(['period']);
    const NO_OP_CODES = new Set(['NONE', 'NOT_THIS', 'UNSURE', '不确定', '都不是']);
    const UNCERTAIN_VALUES = new Set(['不确定', '不清楚', '不知道', '未知', '都不是', '不记得']);

    function cleanText(value) {
        return value == null ? '' : String(value).trim();
    }

    function isObject(value) {
        return value !== null && typeof value === 'object' && !Array.isArray(value);
    }

    function displayPeriod(value) {
        if (Array.isArray(value) && value.length === 2) {
            return value[0] === value[1] ? `第${value[0]}节` : `第${value[0]}-${value[1]}节`;
        }
        return cleanText(value) || '未提供';
    }

    function initialQuestion() {
        return {
            kind: 'memory',
            prompt: '你还记得哪类信息？',
            options: [
                {code: 'A', label: '听课日期', value: 'date'},
                {code: 'B', label: '授课教师', value: 'teacher'},
                {code: 'C', label: '教室', value: 'room'},
            ],
            allow_custom: true,
        };
    }

    function initListeningAssistant() {
        const root = document.querySelector('[data-listening-assistant]');
        const form = document.getElementById('lectureForm');
        if (!root || !form) {
            return;
        }

        const elements = {
            panel: root.querySelector('[data-assistant-panel]'),
            toggle: root.querySelector('[data-assistant-toggle]'),
            toggleHint: root.querySelector('[data-assistant-toggle-hint]'),
            stateLabel: root.querySelector('[data-assistant-state-label]'),
            status: root.querySelector('[data-assistant-status]'),
            guide: root.querySelector('[data-assistant-guide]'),
            questionRegion: root.querySelector('[data-assistant-question-region]'),
            question: root.querySelector('[data-assistant-question]'),
            options: root.querySelector('[data-assistant-options]'),
            custom: root.querySelector('[data-assistant-custom]'),
            customLabel: root.querySelector('[data-assistant-custom-label]'),
            customHelp: root.querySelector('[data-assistant-custom-help]'),
            customInput: root.querySelector('[data-assistant-custom-input]'),
            customSubmit: root.querySelector('[data-assistant-custom-submit]'),
            customCancel: root.querySelector('[data-assistant-custom-cancel]'),
            history: root.querySelector('[data-assistant-history]'),
            progress: root.querySelector('[data-assistant-progress]'),
            back: root.querySelector('[data-assistant-back]'),
            candidateConfirm: root.querySelector('[data-assistant-candidate-confirm]'),
            candidateSummary: root.querySelector('[data-assistant-candidate-summary]'),
            source: root.querySelector('[data-assistant-source]'),
            conflict: root.querySelector('[data-assistant-conflict]'),
            backupAckWrap: root.querySelector('[data-assistant-backup-ack-wrap]'),
            backupAck: root.querySelector('[data-assistant-backup-ack]'),
            roomChoiceWrap: root.querySelector('[data-assistant-room-choice-wrap]'),
            periodOverrideWrap: root.querySelector('[data-assistant-period-override-wrap]'),
            periodOverride: root.querySelector('[data-assistant-period-override]'),
            reviewHint: root.querySelector('[data-assistant-review-hint]'),
            confirm: root.querySelector('[data-assistant-confirm]'),
            manual: root.querySelector('[data-assistant-manual-view]'),
            payload: form.querySelector('#assistant_payload'),
            semester: root.querySelector('[data-assistant-semester]') || root.querySelector('#assistantSemester'),
        };

        const guideState = {
            state: null,
            question: initialQuestion(),
            history: [],
            requestId: 0,
            controller: null,
            customMode: false,
            candidates: [],
            selectedCandidateId: '',
            lastResponse: null,
            assistantFilledFields: new Set(),
            assistantFilledGroups: new Set(),
        };

        function setStatus(message, tone) {
            if (!elements.status) {
                return;
            }
            elements.status.textContent = message || '';
            elements.status.dataset.tone = tone || 'info';
        }

        function setStateLabel(stage) {
            if (!elements.stateLabel) {
                return;
            }
            const labels = {question: '引导', candidate: '候选', confirm: '确认', manual: '手动', done: '已完成', error: '错误'};
            elements.stateLabel.textContent = labels[stage] || '引导';
        }

        function isCurrentRequest(requestId) {
            return requestId === guideState.requestId;
        }

        function invalidateRequests() {
            guideState.requestId += 1;
            if (guideState.controller) {
                guideState.controller.abort();
                guideState.controller = null;
            }
            return guideState.requestId;
        }

        function beginRequest() {
            const requestId = invalidateRequests();
            guideState.controller = new AbortController();
            return {requestId, signal: guideState.controller.signal};
        }

        async function requestJson(url, payload, request) {
            const headers = new Headers({'Accept': 'application/json', 'Content-Type': 'application/json'});
            const csrfMeta = document.querySelector('meta[name="csrf-token"]');
            const csrfToken = csrfMeta ? csrfMeta.getAttribute('content') : '';
            if (csrfToken) {
                headers.set('X-CSRFToken', csrfToken);
            }
            const response = await fetch(url, {
                method: 'POST',
                headers,
                body: JSON.stringify(payload),
                signal: request.signal,
            });
            let result;
            try {
                result = await response.json();
            } catch (error) {
                throw new Error('服务器返回了无法读取的结果');
            }
            if (!response.ok || !result || result.success !== true) {
                throw new Error((result && result.message) || '听课助手请求失败');
            }
            return result.data || {};
        }

        function readSemester() {
            return cleanText(elements.semester && elements.semester.value)
                || cleanText(root.dataset.assistantSemester)
                || cleanText(document.body.dataset.assistantSemester);
        }

        function readKnownFacts() {
            const values = {
                date: cleanText(form.querySelector('#lecture_date')?.value),
                teacher: cleanText(form.querySelector('#teacher_name')?.value),
                room: cleanText(form.querySelector('#lecture_location')?.value),
                period: cleanText(form.querySelector('#class_period')?.value),
                student_grade_class: cleanText(form.querySelector('#student_grade_class')?.value),
            };
            return Object.fromEntries(Object.entries(values).filter(([, value]) => value));
        }

        function knownFactsFromHistory(history) {
            const facts = {};
            history.forEach((entry) => {
                if (FACT_KINDS.includes(entry.kind) && entry.value) {
                    facts[entry.kind] = entry.value;
                }
            });
            return facts;
        }

        function setBusy(button, busy, label) {
            if (!button) {
                return;
            }
            button.disabled = busy;
            if (busy) {
                button.dataset.originalLabel = button.textContent;
                button.textContent = label;
            } else if (button.dataset.originalLabel) {
                button.textContent = button.dataset.originalLabel;
                delete button.dataset.originalLabel;
            }
        }

        function appendText(parent, tagName, className, text) {
            const element = document.createElement(tagName);
            if (className) {
                element.className = className;
            }
            element.textContent = cleanText(text);
            parent.appendChild(element);
            return element;
        }

        function renderHistory() {
            if (!elements.history) {
                return;
            }
            elements.history.replaceChildren();
            if (!guideState.history.length) {
                appendText(elements.history, 'p', 'listening-assistant__history-empty', '历史答案摘要：尚未回答');
                return;
            }
            appendText(elements.history, 'p', 'listening-assistant__history-title', '历史答案摘要');
            const list = document.createElement('ol');
            list.className = 'listening-assistant__history-list';
            guideState.history.forEach((entry) => {
                const item = document.createElement('li');
                item.textContent = `${FACT_LABELS[entry.kind] || '答案'}：${entry.value}`;
                list.appendChild(item);
            });
            elements.history.appendChild(list);
        }

        function renderProgress() {
            const count = guideState.state && Number.isInteger(guideState.state.question_count)
                ? guideState.state.question_count : 0;
            if (elements.progress) {
                elements.progress.textContent = `问题 ${count} / ${MAX_QUESTIONS}`;
            }
        }

        function customInputConfig(kind) {
            const configs = {
                date: {type: 'date', label: '自定义听课日期', help: '请选择 YYYY-MM-DD 日期。'},
                teacher: {type: 'text', label: '自定义授课教师', help: '请输入你记得的教师姓名。'},
                room: {type: 'text', label: '自定义教室', help: '请输入你记得的教室。'},
                period: {type: 'text', label: '自定义节次', help: '例如：第3-4节。'},
                student_grade_class: {type: 'text', label: '自定义听课班级', help: '请输入你记得的班级。'},
                memory: {type: 'text', label: '自定义信息', help: '初始问题请选择类别；无法确定时可直接手动填写。'},
            };
            return configs[kind] || configs.memory;
        }

        function showCustomInput(show) {
            guideState.customMode = show;
            if (elements.custom) {
                elements.custom.hidden = !show;
            }
            if (show && elements.customInput) {
                elements.customInput.focus();
            }
        }

        function renderQuestion() {
            const question = guideState.question;
            const stage = guideState.state && guideState.state.stage;
            const visible = Boolean(question) && (stage === 'question' || stage === 'candidate' || !guideState.state);
            if (elements.questionRegion) {
                elements.questionRegion.hidden = !visible;
            }
            if (!visible || !question) {
                showCustomInput(false);
                return;
            }
            elements.question.textContent = cleanText(question.prompt) || '请选择一项';
            elements.options.replaceChildren();
            const options = Array.isArray(question.options) ? question.options : [];
            options.forEach((option) => {
                if (!isObject(option) || !cleanText(option.code)) {
                    return;
                }
                const button = document.createElement('button');
                button.type = 'button';
                button.className = option.code === 'D' ? 'btn btn-outline-secondary' : 'btn btn-outline-primary';
                button.dataset.assistantOption = cleanText(option.code);
                button.setAttribute('aria-label', `${cleanText(option.code)} ${cleanText(option.label)}`);
                button.textContent = `${cleanText(option.code)}. ${cleanText(option.label)}`;
                elements.options.appendChild(button);
            });
            if (question.allow_custom !== false && !options.some((option) => cleanText(option.code) === 'D')) {
                const customButton = document.createElement('button');
                customButton.type = 'button';
                customButton.className = 'btn btn-outline-secondary';
                customButton.dataset.assistantOption = 'D';
                customButton.setAttribute('aria-label', question.kind === 'memory' ? 'D 我自己填写' : 'D 都不是或手动填写');
                customButton.textContent = question.kind === 'memory' ? 'D. 我自己填写' : 'D. 都不是 / 手动填写';
                elements.options.appendChild(customButton);
            }
            const config = customInputConfig(question.kind);
            elements.customLabel.textContent = config.label;
            elements.customHelp.textContent = config.help;
            elements.customInput.type = config.type;
            elements.customInput.placeholder = config.type === 'date' ? '' : config.help;
            showCustomInput(guideState.customMode);
        }

        function appendCandidateMeta(parent, label, value) {
            const row = document.createElement('div');
            row.className = 'listening-assistant__candidate-meta';
            appendText(row, 'span', 'listening-assistant__meta-label', label);
            appendText(row, 'strong', 'listening-assistant__meta-value', value || '未提供');
            parent.appendChild(row);
        }

        function renderCandidateConfirm() {
            const responseState = guideState.state;
            const show = responseState && (responseState.stage === 'confirm' || responseState.stage === 'done');
            if (elements.candidateConfirm) {
                elements.candidateConfirm.hidden = !show;
            }
            if (!show) {
                return;
            }
            const selectedId = Array.isArray(responseState.candidate_ids) ? cleanText(responseState.candidate_ids[0]) : '';
            const candidate = guideState.candidates.find((item) => cleanText(item && item.candidate_id) === selectedId);
            if (!candidate) {
                elements.candidateSummary.replaceChildren();
                appendText(elements.candidateSummary, 'p', 'alert alert-warning', '服务器没有返回已选择的候选，请返回上一问。');
                elements.confirm.disabled = true;
                return;
            }
            guideState.selectedCandidateId = selectedId;
            elements.candidateSummary.replaceChildren();
            appendText(elements.candidateSummary, 'h3', 'listening-assistant__candidate-title', cleanText(candidate.course_title) || '未命名课程');
            const meta = document.createElement('div');
            meta.className = 'listening-assistant__candidate-meta-grid';
            appendCandidateMeta(meta, '教师', candidate.teacher_name);
            appendCandidateMeta(meta, '教室', candidate.room);
            appendCandidateMeta(meta, '节次', displayPeriod(candidate.period));
            appendCandidateMeta(meta, '班级', candidate.student_grade_class);
            appendCandidateMeta(meta, '日期', candidate.lecture_date);
            elements.candidateSummary.appendChild(meta);
            if (candidate.needs_confirmation === true) {
                appendText(elements.candidateSummary, 'p', 'listening-assistant__review-hint', '该候选需要人工确认后才能写入正式表单。');
            }

            elements.source.replaceChildren();
            const source = candidate.source_kind === 'backup' ? SOURCE_LABELS.backup : (candidate.source_label || SOURCE_LABELS.primary);
            appendText(elements.source, 'p', 'listening-assistant__source-text', `来源：${source}`);
            if (candidate.source_batch_id) {
                appendText(elements.source, 'p', 'listening-assistant__source-batch', `来源批次：${candidate.source_batch_id}`);
            }

            elements.conflict.replaceChildren();
            const conflicts = Array.isArray(candidate.conflicts) ? candidate.conflicts.filter(Boolean) : [];
            if (conflicts.length) {
                appendText(elements.conflict, 'strong', 'listening-assistant__conflict-title', '冲突与核对：');
                const list = document.createElement('ul');
                conflicts.forEach((conflict) => {
                    appendText(list, 'li', '', CONFLICT_LABELS[conflict] || `课表存在差异：${conflict}`);
                });
                elements.conflict.appendChild(list);
            } else {
                appendText(elements.conflict, 'p', 'listening-assistant__no-conflict', '未发现服务器标记的冲突。');
            }

            const isBackup = candidate.source_kind === 'backup';
            elements.backupAckWrap.hidden = !isBackup;
            const formRoom = cleanText(form.querySelector('#lecture_location')?.value);
            elements.roomChoiceWrap.hidden = !formRoom || formRoom === cleanText(candidate.room);
            elements.periodOverrideWrap.hidden = false;
            if (elements.periodOverride && !elements.periodOverride.value) {
                elements.periodOverride.placeholder = displayPeriod(candidate.period);
            }
            if (responseState.stage === 'done') {
                elements.confirm.disabled = true;
                elements.confirm.textContent = '已确认';
            } else {
                elements.confirm.textContent = '确认并补充空白字段';
            }
            updateConfirmControls();
        }

        function updateConfirmControls() {
            if (!guideState.state || guideState.state.stage !== 'confirm' || !guideState.selectedCandidateId) {
                return;
            }
            const candidate = guideState.candidates.find((item) => cleanText(item && item.candidate_id) === guideState.selectedCandidateId);
            const missing = [];
            if (candidate && candidate.source_kind === 'backup' && !elements.backupAck.checked) {
                missing.push('请勾选备用来源确认');
            }
            if (!elements.roomChoiceWrap.hidden && !root.querySelector('[data-assistant-room-choice]:checked')) {
                missing.push('请选择教室处理方式');
            }
            elements.confirm.disabled = missing.length > 0;
            if (elements.reviewHint) {
                elements.reviewHint.textContent = missing.length
                    ? missing.join('；')
                    : '确认成功后才会把服务器字段快照补充到正式表单。';
            }
        }

        function renderManual() {
            const manual = guideState.state && guideState.state.stage === 'manual';
            if (elements.manual) {
                elements.manual.hidden = !manual;
            }
        }

        function renderAll() {
            renderHistory();
            renderProgress();
            renderQuestion();
            renderCandidateConfirm();
            renderManual();
            const stage = guideState.state && guideState.state.stage;
            setStateLabel(stage || 'question');
            if (elements.back) {
                elements.back.disabled = guideState.history.length === 0;
            }
            if (elements.guide) {
                elements.guide.hidden = stage === 'manual';
            }
        }

        function applyGuideResult(data, historyEntry) {
            if (!isObject(data)) {
                throw new Error('服务器没有返回可用的引导结果');
            }
            if (historyEntry) {
                guideState.history.push(historyEntry);
            }
            guideState.state = isObject(data.state) ? data.state : null;
            guideState.question = isObject(data.question) ? data.question : null;
            guideState.candidates = Array.isArray(data.candidates) ? data.candidates.filter(isObject) : [];
            guideState.lastResponse = data;
            guideState.customMode = false;
            renderAll();
        }

        async function startGuide(knownFacts, preserveHistory) {
            const semester = readSemester();
            if (!semester) {
                throw new Error('课表学期尚未设置，暂时无法查询；可以直接手动填写。');
            }
            const request = beginRequest();
            try {
                const data = await requestJson(GUIDE_ENDPOINTS.start, {
                    known_facts: knownFacts || {},
                    semester,
                }, request);
                if (!isCurrentRequest(request.requestId)) {
                    return false;
                }
                if (!preserveHistory) {
                    guideState.history = [];
                }
                applyGuideResult(data);
                setStatus('状态：引导已更新。请选择一个答案。', 'info');
                return true;
            } finally {
                if (isCurrentRequest(request.requestId)) {
                    guideState.controller = null;
                }
            }
        }

        async function ensureStarted() {
            if (guideState.state) {
                return true;
            }
            return startGuide(readKnownFacts(), false);
        }

        function answerValue(option) {
            return cleanText(option && (option.label || option.value));
        }

        async function sendAnswer(optionCode, customValue, historyEntry) {
            if (!guideState.state || !guideState.question) {
                return;
            }
            const semester = readSemester();
            if (!semester) {
                throw new Error('课表学期尚未设置，暂时无法查询；可以直接手动填写。');
            }
            const payload = {
                state: guideState.state,
                question_kind: cleanText(guideState.question.kind),
                option_code: optionCode == null ? null : optionCode,
                custom_value: customValue == null ? null : customValue,
                semester,
            };
            const request = beginRequest();
            try {
                const data = await requestJson(GUIDE_ENDPOINTS.answer, payload, request);
                if (!isCurrentRequest(request.requestId)) {
                    return;
                }
                applyGuideResult(data, historyEntry);
                setStatus('状态：引导已更新。服务器已重新计算候选。', 'info');
            } finally {
                if (isCurrentRequest(request.requestId)) {
                    guideState.controller = null;
                }
            }
        }

        async function answerOption(optionCode) {
            const started = await ensureStarted();
            if (!started || !guideState.question) {
                return;
            }
            const code = cleanText(optionCode);
            const question = guideState.question;
            if (code === 'D') {
                if (question.kind === 'memory') {
                    openManual();
                } else {
                    showCustomInput(true);
                }
                return;
            }
            if (NO_OP_CODES.has(code)) {
                openManual();
                return;
            }
            const option = (Array.isArray(question.options) ? question.options : [])
                .find((item) => cleanText(item && item.code) === code);
            if (!option) {
                setStatus('当前问题已更新，请重新选择。', 'error');
                return;
            }
            await sendAnswer(code, null, {
                kind: question.kind === 'candidate' ? 'candidate' : question.kind,
                value: answerValue(option),
            });
        }

        async function submitCustomAnswer() {
            const started = await ensureStarted();
            if (!started || !guideState.question) {
                return;
            }
            const value = cleanText(elements.customInput && elements.customInput.value);
            if (!value) {
                setStatus('请先填写这一项，或返回选择其他答案。', 'error');
                elements.customInput?.focus();
                return;
            }
            if (UNCERTAIN_VALUES.has(value)) {
                openManual();
                return;
            }
            const question = guideState.question;
            await sendAnswer(null, value, {
                kind: question.kind,
                value,
            });
        }

        function openManual() {
            invalidateRequests();
            guideState.state = {
                known_facts: guideState.state && guideState.state.known_facts ? guideState.state.known_facts : {},
                candidate_ids: guideState.state && Array.isArray(guideState.state.candidate_ids) ? guideState.state.candidate_ids : [],
                asked_question_kinds: guideState.state && Array.isArray(guideState.state.asked_question_kinds) ? guideState.state.asked_question_kinds : [],
                question_count: guideState.state && Number.isInteger(guideState.state.question_count) ? guideState.state.question_count : 0,
                stage: 'manual',
            };
            guideState.customMode = false;
            guideState.question = null;
            renderAll();
            setStatus('状态：手动。助手不会把不确定信息写入正式表单。', 'info');
        }

        async function returnToPrevious() {
            if (!guideState.history.length) {
                return;
            }
            invalidateRequests();
            guideState.history.pop();
            const facts = knownFactsFromHistory(guideState.history);
            guideState.state = null;
            guideState.question = initialQuestion();
            guideState.candidates = [];
            guideState.selectedCandidateId = '';
            renderAll();
            try {
                await startGuide(facts, true);
                setStatus('状态：已返回上一问。', 'info');
            } catch (error) {
                setStatus(error.message || '无法返回上一问，请手动填写。', 'error');
            }
        }

        function queryForConfirmation() {
            const facts = guideState.state && isObject(guideState.state.known_facts)
                ? guideState.state.known_facts : {};
            const query = {};
            if (facts.date) query.lecture_date = facts.date;
            if (facts.teacher) query.teacher_name = facts.teacher;
            if (facts.room) query.room = facts.room;
            if (facts.period) query.period = facts.period;
            if (facts.student_grade_class) query.student_grade_class = facts.student_grade_class;
            const semester = readSemester();
            if (semester) query.semester = semester;
            return query;
        }

        function collectOverrides() {
            const overrides = {};
            const roomChoice = root.querySelector('[data-assistant-room-choice]:checked');
            if (roomChoice && roomChoice.value === 'manual') {
                overrides.lecture_location = cleanText(form.querySelector('#lecture_location')?.value);
            }
            const periodOverride = cleanText(elements.periodOverride && elements.periodOverride.value);
            if (periodOverride) {
                overrides.period = periodOverride;
            }
            return overrides;
        }

        function buildConfirmationPayload(candidate) {
            const sourceKind = candidate.source_kind === 'backup' ? 'backup' : 'primary';
            const payload = {
                query: queryForConfirmation(),
                rejected_ids: [],
                source_kind: sourceKind,
                candidate_id: cleanText(candidate.candidate_id),
                overrides: collectOverrides(),
                template_version: TEMPLATE_VERSION,
                stage: 'confirmed',
                acknowledged_source: sourceKind === 'backup' && elements.backupAck.checked,
                explicit_fallback: sourceKind === 'backup',
            };
            if (sourceKind === 'backup') {
                payload.source_batch_id = cleanText(candidate.source_batch_id);
                payload.fallback_reason = 'no_result';
            }
            const semester = readSemester();
            if (semester) payload.semester = semester;
            return payload;
        }

        function fieldIsBlank(element) {
            return element && !cleanText(element.value);
        }

        function setBlankField(id, value, changed) {
            const element = form.querySelector(`#${id}`);
            if (!element || value == null || !cleanText(value) || !fieldIsBlank(element)) {
                return;
            }
            element.value = String(value);
            changed.push(element);
            guideState.assistantFilledFields.add(id);
        }

        function applyFieldSnapshot(snapshot) {
            if (!isObject(snapshot)) {
                throw new Error('服务器没有返回可用的字段快照');
            }
            const changed = [];
            const dateInput = form.querySelector('#lecture_date');
            const startPeriod = form.querySelector('#start_period');
            const endPeriod = form.querySelector('#end_period');
            const classPeriod = form.querySelector('#class_period');
            setBlankField('lecture_date', snapshot.lecture_date, changed);
            if (fieldIsBlank(startPeriod) && fieldIsBlank(endPeriod) && fieldIsBlank(classPeriod) && snapshot.class_period) {
                const match = String(snapshot.class_period).match(/第(\d+)(?:-(\d+))?节/);
                if (match) {
                    startPeriod.value = match[1];
                    endPeriod.value = match[2] || match[1];
                    classPeriod.value = snapshot.class_period;
                    changed.push(startPeriod, endPeriod, classPeriod);
                    guideState.assistantFilledGroups.add('period');
                }
            }
            [
                ['lecture_location', 'lecture_location'],
                ['teacher_name', 'teacher_name'],
                ['teacher_college', 'teacher_college'],
                ['course_title', 'course_title'],
                ['student_grade_class', 'student_grade_class'],
            ].forEach(([id, key]) => setBlankField(id, snapshot[key], changed));
            if (typeof updateDateWithWeekday === 'function' && cleanText(dateInput?.value)) {
                updateDateWithWeekday();
            }
            if (typeof updateClassPeriod === 'function' && cleanText(startPeriod?.value) && cleanText(endPeriod?.value)) {
                updateClassPeriod();
            }
            if (changed.length) {
                form.dispatchEvent(new Event('input', {bubbles: true}));
            }
        }

        function storeAssistantPayload(responseData, requestPayload) {
            if (!elements.payload) {
                return;
            }
            const confirmation = isObject(responseData.confirmation) ? responseData.confirmation : {};
            const stored = {
                stage: 'confirmed',
                query: isObject(responseData.query) ? responseData.query : requestPayload.query,
                rejected_ids: [],
                source_kind: cleanText(confirmation.source_kind || requestPayload.source_kind),
                candidate_id: cleanText(confirmation.candidate_id || requestPayload.candidate_id),
                overrides: isObject(responseData.overrides) ? responseData.overrides : requestPayload.overrides,
                assistant_filled_fields: Array.from(guideState.assistantFilledFields)
                    .filter((fieldName) => ASSISTANT_FILLED_FIELD_WHITELIST.includes(fieldName)),
                assistant_filled_groups: Array.from(guideState.assistantFilledGroups)
                    .filter((groupName) => ASSISTANT_FILLED_GROUP_WHITELIST.includes(groupName)),
                template_version: cleanText(responseData.template_version || requestPayload.template_version),
                explicit_fallback: confirmation.explicit_fallback === true,
                acknowledged_source: confirmation.acknowledged_source === true,
            };
            const sourceBatchId = cleanText(confirmation.source_batch_id || requestPayload.source_batch_id);
            const semester = cleanText(confirmation.semester || requestPayload.semester || requestPayload.query.semester);
            const fallbackReason = cleanText(confirmation.fallback_reason || requestPayload.fallback_reason);
            if (sourceBatchId) stored.source_batch_id = sourceBatchId;
            if (semester) stored.semester = semester;
            if (fallbackReason) stored.fallback_reason = fallbackReason;
            elements.payload.value = JSON.stringify(stored);
        }

        function mergePersistedAssistantProvenance(payload) {
            const fields = Array.isArray(payload.assistant_filled_fields) ? payload.assistant_filled_fields : [];
            fields.forEach((fieldName) => {
                if (ASSISTANT_FILLED_FIELD_WHITELIST.includes(fieldName)) guideState.assistantFilledFields.add(fieldName);
            });
            const groups = Array.isArray(payload.assistant_filled_groups) ? payload.assistant_filled_groups : [];
            groups.forEach((groupName) => {
                if (ASSISTANT_FILLED_GROUP_WHITELIST.includes(groupName)) guideState.assistantFilledGroups.add(groupName);
            });
        }

        function syncAssistantClearsBeforeSubmit() {
            if (!elements.payload || !cleanText(elements.payload.value)) return;
            let payload;
            try {
                payload = JSON.parse(elements.payload.value);
            } catch (error) {
                return;
            }
            if (!isObject(payload)) return;
            mergePersistedAssistantProvenance(payload);
            const overrides = isObject(payload.overrides) ? {...payload.overrides} : {};
            let changed = false;
            guideState.assistantFilledFields.forEach((fieldName) => {
                const field = form.querySelector(`#${fieldName}`);
                if (field && fieldIsBlank(field) && overrides[fieldName] !== null) {
                    overrides[fieldName] = null;
                    changed = true;
                    if (fieldName === 'lecture_date') {
                        const dateDisplay = form.querySelector('#lecture_date_display');
                        if (dateDisplay) dateDisplay.value = '';
                    }
                }
            });
            if (guideState.assistantFilledGroups.has('period')) {
                const startPeriod = form.querySelector('#start_period');
                const endPeriod = form.querySelector('#end_period');
                if (fieldIsBlank(startPeriod) && fieldIsBlank(endPeriod)) {
                    const classPeriod = form.querySelector('#class_period');
                    if (classPeriod) classPeriod.value = '';
                    if (overrides.period !== null) {
                        overrides.period = null;
                        changed = true;
                    }
                }
            }
            if (changed) {
                payload.overrides = overrides;
                elements.payload.value = JSON.stringify(payload);
            }
        }

        async function confirmSelection() {
            if (!guideState.state || guideState.state.stage !== 'confirm') return;
            const candidate = guideState.candidates.find((item) => cleanText(item && item.candidate_id) === guideState.selectedCandidateId);
            if (!candidate) {
                setStatus('当前候选已失效，请返回上一问。', 'error');
                return;
            }
            updateConfirmControls();
            if (elements.confirm.disabled) {
                setStatus('状态：确认。请先完成页面上标出的确认项。', 'error');
                return;
            }
            const requestPayload = buildConfirmationPayload(candidate);
            const request = beginRequest();
            setBusy(elements.confirm, true, '确认中…');
            try {
                const data = await requestJson(GUIDE_ENDPOINTS.confirm, requestPayload, request);
                if (!isCurrentRequest(request.requestId)) return;
                applyFieldSnapshot(data.field_snapshot);
                storeAssistantPayload(data, requestPayload);
                guideState.state = {...guideState.state, stage: 'done'};
                renderAll();
                setStatus('状态：已完成。服务器已重新核验，空白基础字段已补充。', 'info');
            } catch (error) {
                if (error && error.name !== 'AbortError' && isCurrentRequest(request.requestId)) {
                    setStatus(error.message || '确认失败，请返回上一问后重试。', 'error');
                }
            } finally {
                if (isCurrentRequest(request.requestId)) {
                    setBusy(elements.confirm, false);
                    guideState.controller = null;
                }
            }
        }

        function handleClick(event) {
            const target = event.target instanceof Element ? event.target : null;
            if (!target || !root.contains(target)) return;
            if (target.closest('[data-assistant-toggle]')) {
                const expanded = elements.toggle.getAttribute('aria-expanded') === 'true';
                elements.toggle.setAttribute('aria-expanded', String(!expanded));
                elements.panel.hidden = expanded;
                elements.toggleHint.textContent = expanded ? '展开' : '收起';
                return;
            }
            const option = target.closest('[data-assistant-option]');
            if (option) {
                answerOption(cleanText(option.dataset.assistantOption)).catch((error) => setStatus(error.message || '回答失败，请手动填写。', 'error'));
                return;
            }
            if (target.closest('[data-assistant-custom-submit]')) {
                submitCustomAnswer().catch((error) => setStatus(error.message || '自定义回答失败，请手动填写。', 'error'));
                return;
            }
            if (target.closest('[data-assistant-custom-cancel]')) {
                showCustomInput(false);
                return;
            }
            if (target.closest('[data-assistant-back]') || target.closest('[data-assistant-back-to-guide]')) {
                returnToPrevious();
                return;
            }
            if (target.closest('[data-assistant-confirm]')) {
                confirmSelection();
                return;
            }
            if (target.closest('[data-assistant-none]')) {
                openManual();
                return;
            }
            if (target.closest('[data-assistant-restart]')) {
                invalidateRequests();
                guideState.state = null;
                guideState.question = initialQuestion();
                guideState.history = [];
                guideState.candidates = [];
                guideState.selectedCandidateId = '';
                renderAll();
                setStatus('状态：引导已重置。', 'info');
            }
        }

        function handleKeydown(event) {
            const target = event.target instanceof Element ? event.target : null;
            if (!target) return;
            const option = target.closest('[data-assistant-option]');
            if (option && (event.key === 'Enter' || event.key === ' ')) {
                event.preventDefault();
                option.click();
            }
            if (event.key === 'Enter' && target === elements.customInput) {
                event.preventDefault();
                elements.customSubmit?.click();
            }
        }

        function handleChange(event) {
            const target = event.target;
            if (!target) return;
            if (target.matches('[data-assistant-backup-ack], [data-assistant-room-choice], [data-assistant-period-override]')) {
                updateConfirmControls();
            }
        }

        root.addEventListener('click', handleClick);
        root.addEventListener('keydown', handleKeydown);
        root.addEventListener('change', handleChange);
        form.addEventListener('submit', syncAssistantClearsBeforeSubmit, true);
        if (elements.toggle) {
            elements.toggle.setAttribute('aria-expanded', 'true');
        }
        renderAll();
        if (readSemester()) {
            startGuide(readKnownFacts(), false).catch((error) => {
                if (error && error.name !== 'AbortError') setStatus(error.message || '引导启动失败，可直接手动填写。', 'error');
            });
        }
    }

    document.addEventListener('DOMContentLoaded', initListeningAssistant);
}());
