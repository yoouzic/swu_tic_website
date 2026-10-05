(function () {
    'use strict';

    const GUIDE_ENDPOINTS = Object.freeze({
        start: '/user/api/listening-assistant/guide/start',
        answer: '/user/api/listening-assistant/guide/answer',
        candidates: '/user/api/listening-assistant/candidates',
        fallback: '/user/api/listening-assistant/fallback',
        confirm: '/user/api/listening-assistant/confirm',
    });
    const TEMPLATE_VERSION = 'task4-v1';
    const MAX_QUESTIONS = 4;
    const SEMESTER_STORAGE_KEY = 'swu_tic_listening_assistant_semester_v1';
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
    const UNCERTAIN_VALUES = new Set(['不确定', '不清楚', '不知道', '我不知道', '跳过', '跳过此题', '未知', '都不是', '不记得']);

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
            settings: root.querySelector('[data-assistant-settings]'),
            errorActions: root.querySelector('[data-assistant-error-actions]'),
            continueReview: root.querySelector('[data-assistant-continue-review]'),
            draftNotice: root.querySelector('[data-assistant-draft-notice]'),
            draftMessage: root.querySelector('[data-assistant-draft-message]'),
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
            semesterError: root.querySelector('[data-assistant-semester-error]'),
            knownFacts: root.querySelector('[data-assistant-known-facts]'),
            knownFactsSummary: root.querySelector('[data-assistant-known-facts-summary]'),
            back: root.querySelector('[data-assistant-back]'),
            candidateConfirm: root.querySelector('[data-assistant-candidate-confirm]'),
            candidateSummary: root.querySelector('[data-assistant-candidate-summary]'),
            comparison: root.querySelector('[data-assistant-comparison]'),
            evidenceTitle: root.querySelector('[data-assistant-evidence-title]'),
            source: root.querySelector('[data-assistant-source]'),
            conflict: root.querySelector('[data-assistant-conflict]'),
            backupAckWrap: root.querySelector('[data-assistant-backup-ack-wrap]'),
            backupAck: root.querySelector('[data-assistant-backup-ack]'),
            roomChoiceWrap: root.querySelector('[data-assistant-room-choice-wrap]'),
            dateOverrideWrap: root.querySelector('[data-assistant-date-override-wrap]'),
            dateOverride: root.querySelector('[data-assistant-date-override]'),
            periodOverrideWrap: root.querySelector('[data-assistant-period-override-wrap]'),
            periodOverride: root.querySelector('[data-assistant-period-override]'),
            fallback: root.querySelector('[data-assistant-fallback]'),
            fallbackPanel: root.querySelector('[data-assistant-fallback-panel]'),
            fallbackControls: root.querySelector('[data-assistant-fallback-controls]'),
            fallbackDate: root.querySelector('[data-assistant-fallback-date]'),
            fallbackTeacher: root.querySelector('[data-assistant-fallback-teacher]'),
            fallbackHelp: root.querySelector('[data-assistant-fallback-help]'),
            fallbackSubmit: root.querySelector('[data-assistant-fallback-submit]'),
            reviewHint: root.querySelector('[data-assistant-review-hint]'),
            confirm: root.querySelector('[data-assistant-confirm]'),
            none: root.querySelector('[data-assistant-none]'),
            backToGuide: root.querySelector('[data-assistant-back-to-guide]'),
            manual: root.querySelector('[data-assistant-manual-view]'),
            manualMessage: root.querySelector('[data-assistant-manual-message]'),
            editAnswer: root.querySelector('[data-assistant-edit-answer]'),
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
            backupSourceBatchId: '',
            backupRescueAvailable: false,
            backupSelectionMode: false,
            originalRoomForFallback: '',
            fallbackReason: 'rejected_candidates',
            rejectedIds: [],
            assistantFilledFields: new Set(),
            assistantFilledGroups: new Set(),
            assistantQueryFingerprint: '',
            applyingSnapshot: false,
            formFactsDismissed: false,
            draftRestored: false,
            actionBusy: false,
            manualRequested: false,
            recovery: null,
        };

        function setStatus(message, tone) {
            if (!elements.status) {
                return;
            }
            const isError = tone === 'error';
            let text = message || '';
            if (isError && /schedule|source|semester|课表学期/i.test(text)) {
                text = '课表暂未配置或不可用，请联系管理员；也可直接填写表单。';
                if (elements.settings) elements.settings.open = true;
            } else if (isError && /[a-z]{3,}/i.test(text)) {
                text = '这一步暂时未完成，请重试或直接填写表单。';
            }
            elements.status.textContent = text;
            elements.status.hidden = !text || (!isError && /^状态：/.test(text));
            elements.status.dataset.tone = tone || 'info';
            if (elements.errorActions) elements.errorActions.hidden = !isError;
        }

        function setSemesterError(message) {
            if (!elements.semesterError) {
                return;
            }
            elements.semesterError.textContent = message || '';
            elements.semesterError.hidden = !message;
            if (message && elements.settings) elements.settings.open = true;
        }

        function setStateLabel(stage) {
            if (!elements.stateLabel) {
                return;
            }
            const labels = {question: '引导', candidate: '候选', confirm: '确认', manual: '手动', done: '已完成', error: '错误'};
            elements.stateLabel.textContent = labels[stage] || '引导';
        }

        function readRememberedSemester() {
            try {
                return cleanText(window.localStorage.getItem(SEMESTER_STORAGE_KEY));
            } catch (error) {
                return '';
            }
        }

        function rememberSemester(value) {
            try {
                const semester = cleanText(value);
                if (semester) {
                    window.localStorage.setItem(SEMESTER_STORAGE_KEY, semester);
                } else {
                    window.localStorage.removeItem(SEMESTER_STORAGE_KEY);
                }
            } catch (error) {
                // 隐私模式或浏览器策略可能禁用 localStorage，不影响正常填写。
            }
        }

        function hydrateRememberedSemester(value) {
            if (!elements.semester || cleanText(elements.semester.value)) {
                return;
            }
            const semester = cleanText(root.dataset.assistantSemester);
            if (semester) {
                elements.semester.value = semester;
            }
        }

        function hasMeaningfulDraft(data) {
            if (!isObject(data)) {
                return false;
            }
            return ['lecture_date', 'class_period', 'lecture_location', 'teacher_name', 'course_title', 'student_grade_class', 'assistant']
                .some((key) => isObject(data[key]) || Boolean(cleanText(data[key])));
        }

        function formFactsSummary(facts) {
            return FACT_KINDS
                .filter((kind) => kind !== 'student_grade_class' && cleanText(facts && facts[kind]))
                .map((kind) => `${FACT_LABELS[kind]}：${cleanText(facts[kind])}`)
                .join(' · ');
        }

        function hasSearchableFormFacts(facts) {
            return Object.keys(facts || {}).filter((key) => FACT_KINDS.includes(key) && cleanText(facts[key])).length >= 2;
        }

        function formContextFingerprint() {
            const values = {
                semester: readSemester(),
                date: cleanText(form.querySelector('#lecture_date')?.value),
                teacher: cleanText(form.querySelector('#teacher_name')?.value),
                room: cleanText(form.querySelector('#lecture_location')?.value),
                period: cleanText(form.querySelector('#class_period')?.value),
                course: cleanText(form.querySelector('#course_title')?.value),
                student_grade_class: cleanText(form.querySelector('#student_grade_class')?.value),
            };
            return JSON.stringify(values);
        }

        function rememberAssistantQueryContext() {
            guideState.assistantQueryFingerprint = formContextFingerprint();
        }

        function selectedCandidate() {
            const stateCandidateId = guideState.state && Array.isArray(guideState.state.candidate_ids)
                ? cleanText(guideState.state.candidate_ids[0]) : '';
            const selectedId = guideState.selectedCandidateId || stateCandidateId;
            return guideState.candidates.find((item) => cleanText(item && item.candidate_id) === selectedId) || null;
        }

        function resetGuideState() {
            invalidateRequests();
            clearAssistantConfirmation();
            guideState.state = null;
            guideState.question = initialQuestion();
            guideState.history = [];
            guideState.candidates = [];
            guideState.selectedCandidateId = '';
            guideState.backupSelectionMode = false;
            guideState.rejectedIds = [];
            guideState.assistantQueryFingerprint = '';
            guideState.formFactsDismissed = false;
            guideState.manualRequested = false;
            guideState.recovery = null;
        }

        function markAssistantStateStale() {
            if (guideState.applyingSnapshot || !guideState.assistantQueryFingerprint) {
                return;
            }
            if (guideState.assistantQueryFingerprint === formContextFingerprint()) {
                return;
            }
            const stage = guideState.state && guideState.state.stage;
            if (!stage || stage === 'manual') {
                guideState.formFactsDismissed = false;
                renderAll();
                return;
            }
            resetGuideState();
            setStatus('已检测到表单信息变化，原候选已失效，请重新查找。', 'info');
            renderAll();
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
            return {requestId, signal: guideState.controller.signal, controller: guideState.controller};
        }

        async function requestJson(url, payload, request) {
            const headers = new Headers({'Accept': 'application/json', 'Content-Type': 'application/json'});
            const csrfMeta = document.querySelector('meta[name="csrf-token"]');
            const csrfToken = csrfMeta ? csrfMeta.getAttribute('content') : '';
            if (csrfToken) {
                headers.set('X-CSRFToken', csrfToken);
            }
            let timedOut = false;
            const timeout = window.setTimeout(() => {
                timedOut = true;
                request.controller.abort();
            }, 15000);
            try {
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
                    throw new Error((result && result.message) || '填表助手请求失败');
                }
                return result.data || {};
            } catch (error) {
                if (timedOut) throw new Error('查询时间较长，请重试；也可以直接填写表单。');
                throw error;
            } finally {
                window.clearTimeout(timeout);
            }
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
                if(entry.skipped)return;
                const value = cleanText(entry.custom_value || entry.value);
                if (FACT_KINDS.includes(entry.kind) && value) {
                    facts[entry.kind] = value;
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

        async function runGuideAction(button, action) {
            if (guideState.actionBusy) return;
            guideState.actionBusy = true;
            root.setAttribute('aria-busy', 'true');
            const controls = Array.from(root.querySelectorAll('[data-assistant-option], [data-assistant-custom-submit], [data-assistant-custom-cancel], [data-assistant-back], [data-assistant-skip], [data-assistant-use-form-facts], [data-assistant-back-to-guide]'));
            const disabled = controls.map((control) => control.disabled);
            setBusy(button, true, '查询中…');
            controls.forEach((control) => { control.disabled = true; });
            setStatus('正在查找课程，请稍候…', 'info');
            try {
                await action();
            } catch (error) {
                if (error.name !== 'AbortError') setStatus(error.message || '查询未完成，请重试。', 'error');
            } finally {
                setBusy(button, false);
                controls.forEach((control, index) => { control.disabled = disabled[index]; });
                if(elements.back)elements.back.disabled=!(guideState.history?.length);
                guideState.actionBusy = false;
                root.setAttribute('aria-busy', 'false');
                if (elements.status?.textContent === '正在查找课程，请稍候…') setStatus('', 'info');
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
            const facts = guideState.history.filter((entry) => FACT_KINDS.includes(entry.kind));
            elements.history.hidden = !facts.length || (guideState.state && ['confirm', 'done', 'manual'].includes(guideState.state.stage));
            if (elements.history.hidden) return;
            if (guideState.state && guideState.state.stage === 'done') {
                appendText(elements.history, 'p', 'listening-assistant__history-title', '本次确认');
                const list = document.createElement('ol');
                list.className = 'listening-assistant__history-list';
                const candidate = selectedCandidate();
                appendText(
                    list,
                    'li',
                    '',
                    candidate ? `已确认：${cleanText(candidate.course_title) || '课表候选'}` : '已确认课表候选',
                );
                elements.history.appendChild(list);
                return;
            }
            if (!guideState.history.length) {
                return;
            }
            const list = document.createElement('ol');
            list.className = 'listening-assistant__history-list';
            facts.forEach((entry) => {
                const item = document.createElement('li');
                const value = cleanText(entry.custom_value || entry.value || entry.answer_code);
                item.textContent = `${FACT_LABELS[entry.kind] || '答案'}：${value}`;
                list.appendChild(item);
            });
            elements.history.appendChild(list);
        }

        function renderProgress() {
            const stage = guideState.state && guideState.state.stage;
            const count = guideState.state && Number.isInteger(guideState.state.question_count)
                ? guideState.state.question_count : 0;
            if (elements.progress) {
                elements.progress.hidden = true;
                elements.progress.textContent = stage === 'done'
                    ? '已完成：候选已确认'
                    : (stage === 'manual' ? '手动填写' : `问题 ${count} / ${MAX_QUESTIONS}`);
            }
        }

        function renderDraftNotice() {
            if (!elements.draftNotice) {
                return;
            }
            elements.draftNotice.hidden = !guideState.draftRestored;
            if (guideState.draftRestored && elements.draftMessage) {
                elements.draftMessage.textContent = '';
            }
        }

        function renderKnownFactsPrompt() {
            if (!elements.knownFacts) {
                return;
            }
            const facts = readKnownFacts();
            const visible = !guideState.state && !guideState.formFactsDismissed && hasSearchableFormFacts(facts);
            elements.knownFacts.hidden = !visible;
            if (visible && elements.knownFactsSummary) {
                elements.knownFactsSummary.textContent = formFactsSummary(facts);
            }
        }

        function customInputConfig(kind) {
            const configs = {
                date: {type: 'date', label: '听课日期', help: ''},
                teacher: {type: 'text', label: '授课教师', help: ''},
                room: {type: 'text', label: '教室', help: '例如：8-309。'},
                period: {type: 'text', label: '实际听课节次', help: '例如：第3-4节。'},
                student_grade_class: {type: 'text', label: '听课班级', help: '请输入班级。'},
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

        function displayedQuestionOptions() {
            const question = guideState.question;
            if (!question) return [];
            const batches = question.option_batches;
            if (Array.isArray(batches) && batches.length > 1) {
                return batches[question.batchIndex || 0] || batches[0];
            }
            return Array.isArray(question.options) ? question.options : [];
        }

        function cycleQuestionOptions() {
            const question = guideState.question;
            if (!question || !Array.isArray(question.option_batches) || question.option_batches.length < 2) return;
            question.batchIndex = ((question.batchIndex || 0) + 1) % question.option_batches.length;
            guideState.customMode = false;
            renderQuestion();
        }

        function renderQuestion() {
            const question = guideState.question;
            const stage = guideState.state && guideState.state.stage;
            const hasFormContext = !guideState.state && !guideState.formFactsDismissed && hasSearchableFormFacts(readKnownFacts());
            const visible = Boolean(question) && !hasFormContext && (stage === 'question' || stage === 'candidate' || !guideState.state);
            if (elements.questionRegion) {
                elements.questionRegion.hidden = !visible;
            }
            if (!visible || !question) {
                showCustomInput(false);
                return;
            }
            elements.question.textContent = cleanText(question.prompt) || '请选择一项';
            const skipButton=root.querySelector('[data-assistant-skip]');
            if(skipButton)skipButton.textContent=question.kind==='memory'?'都不记得，稍后填写':stage==='candidate'?'我不确定，先手动填写':'我不知道，跳过此题';
            const nextBatch = root.querySelector('[data-assistant-next-batch]');
            if (nextBatch) nextBatch.hidden = stage !== 'question' || !Array.isArray(question.option_batches) || question.option_batches.length < 2;
            elements.options.replaceChildren();
            const options = displayedQuestionOptions();
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
                const otherLabel = question.kind === 'memory' ? '我自己填写'
                    : (stage === 'candidate' ? '都不是，手动填写' : `其他${FACT_LABELS[question.kind] || '信息'}`);
                customButton.setAttribute('aria-label', `D ${otherLabel}`);
                customButton.textContent = `D. ${otherLabel}`;
                elements.options.appendChild(customButton);
            }
            const config = customInputConfig(question.kind);
            elements.customLabel.textContent = config.label;
            elements.customHelp.textContent = config.help;
            elements.customHelp.hidden = !config.help;
            elements.customInput.type = config.type;
            if (elements.customInput.dataset.questionKind !== question.kind) {
                elements.customInput.value = '';
                elements.customInput.dataset.questionKind = question.kind;
            }
            elements.customInput.placeholder = config.type === 'date' ? '' : config.help;
            if (!options.length && FACT_KINDS.includes(question.kind) && stage !== 'candidate') {
                elements.options.hidden = true;
                guideState.customMode = true;
            } else {
                elements.options.hidden = false;
            }
            const customCancel = root.querySelector('[data-assistant-custom-cancel]');
            if (customCancel) customCancel.hidden = !options.length;
            showCustomInput(guideState.customMode);
        }

        function appendCandidateMeta(parent, label, value) {
            const row = document.createElement('div');
            row.className = 'listening-assistant__candidate-meta';
            appendText(row, 'span', 'listening-assistant__meta-label', label);
            if (label === '班级' && cleanText(value).length > 90) {
                const details = document.createElement('details');
                details.className = 'listening-assistant__meta-value';
                appendText(details, 'summary', '', `${cleanText(value).split(/[;；]/)[0]}等`);
                appendText(details, 'p', '', value);
                row.appendChild(details);
            } else {
                appendText(row, 'strong', 'listening-assistant__meta-value', value || '未提供');
            }
            parent.appendChild(row);
        }

        function comparableValue(value) {
            const normalized = cleanText(value).normalize ? cleanText(value).normalize('NFKC') : cleanText(value);
            return normalized.replace(/\s+/g, ' ').toLowerCase();
        }

        function renderCandidateComparison(candidate) {
            if (!elements.comparison) {
                return;
            }
            elements.comparison.replaceChildren();
            const rows = [
                ['课程', form.querySelector('#course_title')?.value, candidate.course_title],
                ['教师', form.querySelector('#teacher_name')?.value, candidate.teacher_name],
                ['教室', form.querySelector('#lecture_location')?.value, candidate.room],
                ['节次', form.querySelector('#class_period')?.value, displayPeriod(candidate.period)],
                ['日期', form.querySelector('#lecture_date')?.value, candidate.lecture_date],
                ['班级', form.querySelector('#student_grade_class')?.value, candidate.student_grade_class],
            ];
            const differentRows = rows.filter(([, current, proposed]) => cleanText(current) && cleanText(proposed) && comparableValue(current) !== comparableValue(proposed));
            elements.comparison.hidden = !differentRows.length;
            if (!differentRows.length) return;
            const table = document.createElement('table');
            table.className = 'table table-sm listening-assistant__comparison-table-inner';
            table.setAttribute('aria-label', '当前填写与课表候选对照');
            const thead = document.createElement('thead');
            const headingRow = document.createElement('tr');
            appendText(headingRow, 'th', '', '项目');
            appendText(headingRow, 'th', '', '你已填写');
            appendText(headingRow, 'th', '', '课表候选');
            thead.appendChild(headingRow);
            table.appendChild(thead);
            const tbody = document.createElement('tbody');
            differentRows.forEach(([label, current, proposed]) => {
                const currentValue = cleanText(current) || '未填写';
                const proposedValue = cleanText(proposed) || '未提供';
                const differs = currentValue !== '未填写'
                    && proposedValue !== '未提供'
                    && comparableValue(currentValue) !== comparableValue(proposedValue);
                const row = document.createElement('tr');
                if (differs) {
                    row.className = 'listening-assistant__comparison-different';
                }
                appendText(row, 'th', '', label);
                appendText(row, 'td', '', currentValue);
                appendText(row, 'td', '', proposedValue);
                tbody.appendChild(row);
            });
            table.appendChild(tbody);
            elements.comparison.appendChild(table);
            appendText(elements.comparison, 'p', 'listening-assistant__review-hint', '已填写的内容会保留；不同信息请在下方课程信息中核对。');
        }

        function renderCandidateConfirm() {
            const responseState = guideState.state;
            const show = responseState && (responseState.stage === 'confirm' || responseState.stage === 'done');
            const completed = responseState && responseState.stage === 'done';
            if (elements.candidateConfirm) {
                elements.candidateConfirm.hidden = !show;
                elements.candidateConfirm.classList.toggle('listening-assistant__candidate-confirm--done', completed);
            }
            if (!show) {
                return;
            }
            const selectedId = Array.isArray(responseState.candidate_ids) ? cleanText(responseState.candidate_ids[0]) : '';
            const candidate = guideState.candidates.find((item) => cleanText(item && item.candidate_id) === selectedId);
            if (!candidate) {
                elements.candidateSummary.replaceChildren();
                appendText(elements.candidateSummary, 'p', 'alert alert-warning', '这节课暂时无法确认，请返回重新选择。');
                elements.confirm.disabled = true;
                return;
            }
            guideState.selectedCandidateId = selectedId;
            elements.candidateSummary.replaceChildren();
            if (elements.evidenceTitle) {
                elements.evidenceTitle.textContent = completed ? '已确认的候选与来源' : '确认前请核对课程、教师、教室、节次、班级和来源。';
            }
            appendText(elements.candidateSummary, 'h3', 'listening-assistant__candidate-title', cleanText(candidate.course_title) || '未命名课程');
            const meta = document.createElement('div');
            meta.className = 'listening-assistant__candidate-meta-grid';
            appendCandidateMeta(meta, '教师', candidate.teacher_name);
            appendCandidateMeta(meta, '教室', candidate.room);
            appendCandidateMeta(meta, '节次', displayPeriod(candidate.period));
            appendCandidateMeta(meta, '班级', candidate.student_grade_class);
            appendCandidateMeta(meta, '日期', candidate.lecture_date);
            elements.candidateSummary.appendChild(meta);
            renderCandidateComparison(candidate);
            if (candidate.needs_confirmation === true || candidate.source_kind === 'backup') {
                if (candidate.source_kind === 'backup') appendText(elements.candidateSummary, 'p', 'listening-assistant__review-hint', '旧课表信息，请核对是否与实际一致。');
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
                appendText(elements.conflict, 'strong', 'listening-assistant__conflict-title', '请核对实际听课信息：');
                const list = document.createElement('ul');
                conflicts.forEach((conflict) => {
                    appendText(list, 'li', '', CONFLICT_LABELS[conflict] || '课程信息有差异，请按实际听课情况核对。');
                });
                elements.conflict.appendChild(list);
            }
            elements.conflict.hidden = !conflicts.length;

            const isBackup = candidate.source_kind === 'backup';
            elements.backupAckWrap.hidden = !isBackup;
            const formRoom = cleanText(form.querySelector('#lecture_location')?.value);
            const knownRoom = cleanText(
                guideState.originalRoomForFallback
                || (guideState.state && guideState.state.known_facts && guideState.state.known_facts.room)
                || formRoom,
            );
            const roomConflict = conflicts.some((conflict) => String(conflict).toLowerCase().includes('location'))
                || (
                    isBackup
                    && Boolean(knownRoom)
                    && Boolean(cleanText(candidate.room))
                    && knownRoom !== cleanText(candidate.room)
                );
            elements.roomChoiceWrap.hidden = !roomConflict && (!knownRoom || knownRoom === cleanText(candidate.room));
            const dateConflict = conflicts.includes('date_needs_confirmation');
            const periodConflict = conflicts.some(
                (conflict) => ['period_mismatch', 'venue_period_needs_confirmation'].includes(conflict),
            );
            elements.dateOverrideWrap.hidden = !dateConflict;
            if (!dateConflict && elements.dateOverride) {
                elements.dateOverride.value = '';
            }
            if (elements.dateOverride && dateConflict && !elements.dateOverride.value) {
                elements.dateOverride.placeholder = candidate.lecture_date;
            }
            elements.periodOverrideWrap.hidden = !periodConflict;
            if (elements.periodOverride && !elements.periodOverride.value) {
                elements.periodOverride.placeholder = displayPeriod(candidate.period);
            }
            elements.confirm.hidden = completed;
            if (elements.none) {
                elements.none.hidden = completed;
            }
            if (elements.backToGuide && completed) {
                elements.backToGuide.textContent = '重新查找';
            } else if (elements.backToGuide) {
                elements.backToGuide.textContent = '返回上一问';
            }
            if (completed) {
                elements.confirm.disabled = true;
                elements.confirm.textContent = '已确认';
            } else {
                elements.confirm.textContent = '是这节课，填入表单';
            }
            if (completed) {
                elements.reviewHint.textContent = '课程信息已填入。';
            } else {
                updateConfirmControls();
            }
            if (elements.continueReview) elements.continueReview.hidden = !completed;
        }

        function updateConfirmControls() {
            if (!guideState.state || guideState.state.stage !== 'confirm' || !guideState.selectedCandidateId) {
                return;
            }
            const candidate = guideState.candidates.find((item) => cleanText(item && item.candidate_id) === guideState.selectedCandidateId);
            const missing = [];
            if (candidate && candidate.source_kind === 'backup' && !elements.backupAck.checked) {
                missing.push('请确认旧课表信息与实际一致');
            }
            if (!elements.roomChoiceWrap.hidden && !root.querySelector('[data-assistant-room-choice]:checked')) {
                missing.push('请选择教室处理方式');
            }
            const conflicts = candidate && Array.isArray(candidate.conflicts) ? candidate.conflicts : [];
            if (
                conflicts.includes('date_needs_confirmation')
                && !cleanText(elements.dateOverride && elements.dateOverride.value)
            ) {
                missing.push('请选择实际听课日期');
            }
            if (
                conflicts.some((conflict) => ['period_mismatch', 'venue_period_needs_confirmation'].includes(conflict))
                && !cleanText(elements.periodOverride && elements.periodOverride.value)
            ) {
                missing.push('请填写实际听课节次');
            }
            elements.confirm.disabled = missing.length > 0;
            if (elements.reviewHint) {
                elements.reviewHint.textContent = missing.length
                    ? missing.join('；')
                    : '';
            }
        }

        function updateFallbackControls() {
            if (!elements.fallbackControls) {
                return;
            }
            const stage = guideState.state && guideState.state.stage;
            const visible = Boolean(
                guideState.state
                && stage !== 'done'
                && guideState.backupRescueAvailable
                && guideState.backupSourceBatchId,
            );
            if (elements.fallbackPanel) {
                elements.fallbackPanel.hidden = !visible;
            }
            if (!visible) {
                elements.fallbackControls.hidden = true;
                return;
            }
            const facts = guideState.state && isObject(guideState.state.known_facts)
                ? guideState.state.known_facts : {};
            if (!cleanText(elements.fallbackDate?.value) && facts.date) {
                elements.fallbackDate.value = facts.date;
            }
            if (!cleanText(elements.fallbackTeacher?.value) && facts.teacher) {
                elements.fallbackTeacher.value = facts.teacher;
            }
            const ready = Boolean(
                guideState.backupRescueAvailable
                && guideState.backupSourceBatchId
                && readSemester()
                && cleanText(elements.fallbackDate?.value)
                && cleanText(elements.fallbackTeacher?.value),
            );
            if (elements.fallbackSubmit) {
                elements.fallbackSubmit.disabled = !ready;
            }
            if (elements.fallbackHelp) {
                elements.fallbackHelp.textContent = guideState.backupRescueAvailable
                    ? '其他课表可能包含旧信息，请按实际听课情况核对。'
                    : '没有其他课表可查，请直接填写表单。';
            }
        }

        function showFallbackControls() {
            if (!elements.fallbackControls) {
                return;
            }
            elements.fallbackControls.hidden = false;
            updateFallbackControls();
            if (elements.fallbackSubmit && !elements.fallbackSubmit.disabled) {
                elements.fallbackSubmit.focus();
            } else {
                elements.fallbackDate?.focus();
            }
        }

        function backupCandidateLabel(candidate) {
            return [
                cleanText(candidate.course_title) || '未命名课程',
                cleanText(candidate.teacher_name) || '教师未知',
                cleanText(candidate.room) || '教室未知',
                displayPeriod(candidate.period),
            ].join(' · ');
        }

        function selectBackupCandidate(candidateId) {
            const candidate = guideState.candidates.find(
                (item) => cleanText(item && item.candidate_id) === cleanText(candidateId),
            );
            if (!candidate) {
                setStatus('备用候选已失效，请重新查询。', 'error');
                return;
            }
            guideState.selectedCandidateId = cleanText(candidate.candidate_id);
            guideState.state = {
                known_facts: guideState.state && isObject(guideState.state.known_facts)
                    ? guideState.state.known_facts : {},
                candidate_ids: [guideState.selectedCandidateId],
                asked_question_kinds: guideState.state && Array.isArray(guideState.state.asked_question_kinds)
                    ? guideState.state.asked_question_kinds : [],
                question_count: guideState.state && Number.isInteger(guideState.state.question_count)
                    ? guideState.state.question_count : 0,
                stage: 'confirm',
            };
            guideState.question = null;
            renderAll();
            setStatus('状态：确认。请核对备用来源、冲突和覆盖值。', 'info');
            elements.confirm?.focus();
        }

        async function requestFallback() {
            const semester = readSemester();
            const date = cleanText(elements.fallbackDate?.value);
            const teacher = cleanText(elements.fallbackTeacher?.value);
            if (!semester) {
                setSemesterError('请先填写课表学期，再查询备用课表。');
                return;
            }
            if (!date || !teacher) {
                if (elements.fallbackHelp) {
                    elements.fallbackHelp.textContent = '备用查询需要同时填写日期和授课教师。';
                }
                return;
            }
            if (!guideState.backupRescueAvailable || !guideState.backupSourceBatchId) {
                setStatus('当前学期没有可用备用批次，请手动填写。', 'error');
                return;
            }
            guideState.originalRoomForFallback = cleanText(
                guideState.state?.known_facts?.room || form.querySelector('#lecture_location')?.value,
            );
            guideState.fallbackReason = guideState.candidates.length
                ? 'rejected_candidates' : 'no_result';
            guideState.rejectedIds = guideState.candidates
                .map((candidate) => cleanText(candidate && candidate.candidate_id))
                .filter(Boolean);
            const request = beginRequest();
            setBusy(elements.fallbackSubmit, true, '查询中…');
            try {
                const fallbackPayload = {
                    date,
                    teacher,
                    semester,
                    source_batch_id: guideState.backupSourceBatchId,
                    reason: guideState.fallbackReason,
                    explicit_fallback: true,
                    rejected_ids: guideState.rejectedIds.slice(),
                };
                const facts = guideState.state && isObject(guideState.state.known_facts)
                    ? guideState.state.known_facts : {};
                if (facts.period) {
                    fallbackPayload.period = facts.period;
                }
                if (facts.student_grade_class) {
                    fallbackPayload.student_grade_class = facts.student_grade_class;
                }
                const data = await requestJson(GUIDE_ENDPOINTS.fallback, fallbackPayload, request);
                if (!isCurrentRequest(request.requestId)) {
                    return;
                }
                guideState.candidates = Array.isArray(data.candidates) ? data.candidates.filter(isObject) : [];
                guideState.backupSourceBatchId = cleanText(data.source_batch_id || guideState.backupSourceBatchId);
                guideState.backupRescueAvailable = Boolean(guideState.backupSourceBatchId);
                guideState.fallbackReason = cleanText(data.fallback_reason || guideState.fallbackReason);
                guideState.backupSelectionMode = true;
                const previousFacts = guideState.state && isObject(guideState.state.known_facts)
                    ? guideState.state.known_facts : {};
                guideState.state = {
                    known_facts: {
                        date,
                        teacher,
                        ...(previousFacts.period ? {period: previousFacts.period} : {}),
                        ...(previousFacts.student_grade_class
                            ? {student_grade_class: previousFacts.student_grade_class} : {}),
                    },
                    candidate_ids: guideState.candidates.map((candidate) => cleanText(candidate.candidate_id)),
                    asked_question_kinds: ['teacher'],
                    question_count: guideState.state && Number.isInteger(guideState.state.question_count)
                        ? guideState.state.question_count : 0,
                    stage: guideState.candidates.length ? 'candidate' : 'manual',
                };
                guideState.question = guideState.candidates.length ? {
                    kind: 'candidate',
                    prompt: '请选择最符合的备用课表线索：',
                    options: guideState.candidates.slice(0, 3).map((candidate, index) => ({
                        code: String.fromCharCode(65 + index),
                        label: backupCandidateLabel(candidate),
                        value: cleanText(candidate.candidate_id),
                        candidate_count: 1,
                    })),
                    allow_custom: true,
                    custom_label: 'D. 都不是 / 手动填写',
                } : null;
                if (elements.fallbackControls) {
                    elements.fallbackControls.hidden = true;
                }
                renderAll();
                setStatus(
                    guideState.candidates.length
                        ? '状态：候选。请选择备用课表线索。'
                        : '状态：手动。备用课表没有返回候选。',
                    guideState.candidates.length ? 'info' : 'error',
                );
            } catch (error) {
                if (error && error.name !== 'AbortError') {
                    setStatus(error.message || '备用课表查询失败，请手动填写。', 'error');
                }
            } finally {
                if (isCurrentRequest(request.requestId)) {
                    setBusy(elements.fallbackSubmit, false);
                    guideState.controller = null;
                }
            }
        }

        function renderManual() {
            const manual = guideState.state && guideState.state.stage === 'manual';
            if (elements.manual) {
                elements.manual.hidden = !manual;
            }
            if (!manual) return;
            const unmatched = !guideState.manualRequested && !guideState.candidates.length;
            const date = cleanText(guideState.state.known_facts?.date);
            if (elements.manualMessage) {
                elements.manualMessage.textContent = guideState.history?.at(-1)?.skipped
                    ? '暂时记不清也可以继续填写其他内容。已保留确认过的信息，可返回上一题或稍后补充。'
                    : unmatched
                    ? `没有找到${date ? ` ${date} 对应的` : '符合这些信息的'}课程，请核对日期、教师或教室。`
                    : (guideState.manualRequested ? '已切换为手动填写，请在下方补充课程信息。也可以重新开始引导。' : '现有信息还不能确定课程。可以修改答案、重新查找，或在下方手动填写。');
            }
            if (elements.editAnswer) {
                elements.editAnswer.hidden = !guideState.recovery;
                elements.editAnswer.textContent=guideState.history?.at(-1)?.skipped?'返回上一题':'修改上一项';
            }
        }

        function editLastAnswer() {
            if (!guideState.recovery) return;
            invalidateRequests();
            const recovery = guideState.recovery;
            guideState.state = recovery.state;
            guideState.question = recovery.question;
            guideState.candidates = recovery.candidates;
            guideState.history = recovery.history;
            guideState.manualRequested = false;
            guideState.customMode = recovery.question?.kind !== 'memory';
            renderAll();
            setStatus('请修改上一项信息后重新查找。', 'info');
        }

        function renderAll() {
            renderDraftNotice();
            renderHistory();
            renderProgress();
            renderQuestion();
            renderKnownFactsPrompt();
            renderCandidateConfirm();
            renderManual();
            const stage = guideState.state && guideState.state.stage;
            setStateLabel(stage || 'question');
            if (elements.back) {
                elements.back.disabled = guideState.history.length === 0;
                elements.back.hidden = guideState.history.length === 0 || stage === 'confirm' || stage === 'done';
            }
            if (elements.guide) {
                elements.guide.hidden = ['manual', 'confirm', 'done'].includes(stage);
            }
            updateFallbackControls();
        }

        function applyGuideResult(data, historyEntry) {
            if (!isObject(data)) {
                throw new Error('服务器没有返回可用的引导结果');
            }
            if (historyEntry) {
                guideState.recovery = {
                    state: guideState.state,
                    question: guideState.question,
                    candidates: guideState.candidates,
                    history: guideState.history.slice(),
                };
                guideState.history.push({...historyEntry,previous:guideState.recovery});
            }
            guideState.manualRequested = false;
            guideState.state = isObject(data.state) ? data.state : null;
            guideState.question = isObject(data.question) ? data.question : null;
            guideState.candidates = Array.isArray(data.candidates) ? data.candidates.filter(isObject) : [];
            guideState.lastResponse = data;
            guideState.backupSourceBatchId = cleanText(data.backup_source_batch_id);
            guideState.backupRescueAvailable = data.backup_rescue_available === true;
            guideState.backupSelectionMode = false;
            guideState.rejectedIds = [];
            guideState.customMode = false;
            renderAll();
        }

        function announceGuideState() {
            const stage = guideState.state && guideState.state.stage;
            if (stage === 'candidate') {
                setStatus('状态：已找到候选，请选择最符合的一项。', 'info');
            } else if (stage === 'confirm') {
                setStatus('状态：请核对候选、来源和冲突后确认。', 'info');
            } else if (stage === 'manual') {
                setStatus('', 'info');
            } else {
                setStatus('状态：请选择一个答案。', 'info');
            }
        }

        async function startGuide(knownFacts, preserveHistory) {
            const semester = readSemester();
            if (!semester) {
                setSemesterError('课表暂未配置，请联系管理员。');
                throw new Error('课表暂未配置，请联系管理员；也可直接填写表单。');
            }
            setSemesterError('');
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
                rememberAssistantQueryContext();
                announceGuideState();
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
            return startGuide(guideState.formFactsDismissed ? {} : readKnownFacts(), false);
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
                throw new Error('课表暂未配置，请联系管理员；也可直接填写表单。');
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
                rememberAssistantQueryContext();
                announceGuideState();
            } finally {
                if (isCurrentRequest(request.requestId)) {
                    guideState.controller = null;
                }
            }
        }

        async function answerOption(optionCode) {
            if (cleanText(optionCode) === 'D' && (!guideState.state || guideState.question?.kind === 'memory')) {
                openManual();
                return;
            }
            const started = !guideState.state&&guideState.question?.kind==='memory'
                ? await startGuide({},false) : await ensureStarted();
            if (!started || !guideState.question) {
                return;
            }
            const code = cleanText(optionCode);
            const question = guideState.question;
            if (code === 'D') {
                if (question.kind === 'memory' || question.kind === 'candidate') {
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
            const option = displayedQuestionOptions()
                .find((item) => cleanText(item && item.code) === code);
            if (!option) {
                setStatus('当前问题已更新，请重新选择。', 'error');
                return;
            }
            if (guideState.backupSelectionMode) {
                selectBackupCandidate(option.value);
                return;
            }
            // Later batches carry factual values, never reused A/B/C positions
            // from the first batch. The server searches the current source again.
            const laterBatch = (question.batchIndex || 0) > 0 && guideState.state?.stage === 'question';
            await sendAnswer(laterBatch ? null : code, laterBatch ? option.value : null, {
                kind: question.kind === 'candidate' ? 'candidate' : question.kind,
                answer_code: laterBatch ? 'D' : code,
                custom_value: laterBatch ? option.value : null,
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
            if (UNCERTAIN_VALUES.has(value)) {await skipQuestion();return;}
            const question = guideState.question;
            await sendAnswer(null, value, {
                kind: question.kind,
                answer_code: 'D',
                custom_value: value,
                value,
            });
        }

        async function skipQuestion() {
            const started=!guideState.state&&guideState.question?.kind==='memory'
                ? await startGuide({},false) : await ensureStarted();
            if(!started||!guideState.question)return;
            await sendAnswer('SKIP',null,{kind:guideState.question.kind,answer_code:'SKIP',value:'已跳过',skipped:true});
        }

        function clearAssistantConfirmation() {
            if (elements.payload) {
                elements.payload.value = '';
            }
            guideState.assistantFilledFields.clear();
            guideState.assistantFilledGroups.clear();
            form.dispatchEvent(new CustomEvent('lecture-assistant-course-cleared'));
        }

        function openManual() {
            invalidateRequests();
            guideState.manualRequested = true;
            guideState.recovery = null;
            clearAssistantConfirmation();
            guideState.history = [];
            guideState.backupSelectionMode = false;
            guideState.state = {
                known_facts: guideState.state && guideState.state.known_facts ? guideState.state.known_facts : {},
                candidate_ids: [],
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
            clearAssistantConfirmation();
            const last=guideState.history.pop();
            if(last.previous){
                Object.assign(guideState,last.previous);
                guideState.selectedCandidateId='';guideState.manualRequested=false;guideState.customMode=false;
                guideState.backupSelectionMode=false;guideState.rejectedIds=[];guideState.recovery=null;
                renderAll();setStatus('已返回上一题，可以重新选择。','info');return;
            }
            const facts = knownFactsFromHistory(guideState.history);
            guideState.state = null;
            guideState.question = initialQuestion();
            guideState.candidates = [];
            guideState.selectedCandidateId = '';
            guideState.backupSelectionMode = false;
            guideState.rejectedIds = [];
            renderAll();
            try {
                await startGuide(facts, true);
                setStatus('状态：已返回上一问。', 'info');
            } catch (error) {
                setStatus(error.message || '无法返回上一问，请手动填写。', 'error');
            }
        }

        function queryForConfirmation(candidate) {
            const facts = guideState.state && isObject(guideState.state.known_facts)
                ? guideState.state.known_facts : {};
            const query = {};
            if (facts.date || candidate.lecture_date) query.lecture_date = facts.date || candidate.lecture_date;
            if (facts.teacher || candidate.teacher_name) query.teacher_name = facts.teacher || candidate.teacher_name;
            if (facts.room || candidate.room) query.room = facts.room || candidate.room;
            if (facts.period) query.period = facts.period;
            if (facts.student_grade_class) query.student_grade_class = facts.student_grade_class;
            const semester = readSemester();
            if (semester) query.semester = semester;
            return query;
        }

        function collectOverrides(candidate) {
            const overrides = {};
            const roomChoice = root.querySelector('[data-assistant-room-choice]:checked');
            if (roomChoice && roomChoice.value === 'candidate') {
                overrides.room = cleanText(candidate.room);
            } else if (roomChoice && roomChoice.value === 'manual') {
                overrides.lecture_location = cleanText(form.querySelector('#lecture_location')?.value);
            }
            const dateOverride = cleanText(elements.dateOverride && elements.dateOverride.value);
            if (dateOverride) {
                overrides.lecture_date = dateOverride;
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
                query: queryForConfirmation(candidate),
                rejected_ids: guideState.rejectedIds.slice(),
                source_kind: sourceKind,
                candidate_id: cleanText(candidate.candidate_id),
                overrides: collectOverrides(candidate),
                template_version: TEMPLATE_VERSION,
                stage: 'confirmed',
                acknowledged_source: sourceKind === 'backup' && elements.backupAck.checked,
                explicit_fallback: sourceKind === 'backup',
            };
            if (sourceKind === 'backup') {
                payload.source_batch_id = cleanText(candidate.source_batch_id);
                payload.fallback_reason = guideState.fallbackReason || 'rejected_candidates';
                payload.reason = payload.fallback_reason;
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
            if (isObject(guideState.state)) {
                stored.guide_state = {
                    known_facts: isObject(guideState.state.known_facts) ? guideState.state.known_facts : {},
                    candidate_ids: Array.isArray(guideState.state.candidate_ids)
                        ? guideState.state.candidate_ids : [],
                    asked_question_kinds: Array.isArray(guideState.state.asked_question_kinds)
                        ? guideState.state.asked_question_kinds : [],
                    question_count: Number.isInteger(guideState.state.question_count)
                        ? guideState.state.question_count : 0,
                    stage: cleanText(guideState.state.stage) || 'done',
                };
                stored.history = guideState.history.map((entry) => ({
                    kind: cleanText(entry.kind),
                    answer_code: cleanText(entry.answer_code),
                    custom_value: entry.custom_value == null ? null : cleanText(entry.custom_value),
                }));
            }
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
                guideState.applyingSnapshot = true;
                applyFieldSnapshot(data.field_snapshot);
                guideState.applyingSnapshot = false;
                storeAssistantPayload(data, requestPayload);
                guideState.state = {...guideState.state, stage: 'done'};
                guideState.draftRestored = false;
                rememberAssistantQueryContext();
                renderAll();
                form.dispatchEvent(new CustomEvent('lecture-assistant-course-confirmed'));
                setStatus('状态：已完成。服务器已重新核验，空白基础字段已补充。', 'info');
            } catch (error) {
                guideState.applyingSnapshot = false;
                if (error && error.name !== 'AbortError' && isCurrentRequest(request.requestId)) {
                    setStatus(error.message || '确认失败，请返回上一问后重试。', 'error');
                }
            } finally {
                if (isCurrentRequest(request.requestId)) {
                    if (guideState.state && guideState.state.stage === 'done') {
                        elements.confirm.disabled = true;
                        elements.confirm.textContent = '已确认';
                    } else {
                        setBusy(elements.confirm, false);
                    }
                    guideState.controller = null;
                }
            }
        }

        function startNewDraft() {
            resetGuideState();
            guideState.draftRestored = false;
            if (typeof form.reset === 'function') {
                form.reset();
            }
            hydrateRememberedSemester();
            if (typeof updateDateWithWeekday === 'function') {
                updateDateWithWeekday();
            }
            if (typeof updateClassPeriod === 'function') {
                updateClassPeriod();
            }
            if (typeof deleteLectureFormDraft === 'function') {
                deleteLectureFormDraft();
            }
            renderAll();
            setStatus('状态：已新建空白表单，可以开始填写。', 'info');
            form.querySelector('#lecture_date')?.focus();
        }

        function handleDraftLoaded(event) {
            const detail = event && isObject(event.detail) ? event.detail : {};
            const data = isObject(detail.data) ? detail.data : {};
            const assistant = isObject(data.assistant) ? data.assistant : {};
            hydrateRememberedSemester();
            if ((assistant.semester && cleanText(assistant.semester) !== readSemester()) || assistant.source_kind === 'backup') {
                clearAssistantConfirmation();
            }
            guideState.draftRestored = hasMeaningfulDraft(data);
            if (guideState.draftRestored) {
                setStatus('状态：已恢复上次草稿，请确认内容后继续。', 'info');
            }
            renderAll();
        }

        function handleClick(event) {
            const target = event.target instanceof Element ? event.target : null;
            if (!target || !root.contains(target)) return;
            if (target.closest('[data-assistant-next-batch]')) {
                if (!root.getAttribute('aria-busy') || root.getAttribute('aria-busy') !== 'true') cycleQuestionOptions();
                return;
            }
            if (target.closest('[data-assistant-manual-entry]')) {
                openManual();
                form.querySelector('#lecture_date')?.focus();
                return;
            }
            if (target.closest('[data-assistant-toggle]')) {
                const expanded = elements.toggle.getAttribute('aria-expanded') === 'true';
                elements.toggle.setAttribute('aria-expanded', String(!expanded));
                elements.panel.hidden = expanded;
                elements.toggleHint.textContent = expanded ? '展开' : '收起';
                return;
            }
            if (target.closest('[data-assistant-clear-draft]')) {
                startNewDraft();
                return;
            }
            if (target.closest('[data-assistant-use-form-facts]')) {
                guideState.formFactsDismissed = true;
                runGuideAction(target.closest('[data-assistant-use-form-facts]'), () => startGuide(readKnownFacts(), false));
                return;
            }
            if (target.closest('[data-assistant-ignore-form-facts]')) {
                resetGuideState();
                guideState.formFactsDismissed = true;
                renderAll();
                setStatus('状态：请从下面的问题开始回答。', 'info');
                return;
            }
            const option = target.closest('[data-assistant-option]');
            if (option) {
                runGuideAction(option, () => answerOption(cleanText(option.dataset.assistantOption)));
                return;
            }
            if (target.closest('[data-assistant-custom-submit]')) {
                runGuideAction(elements.customSubmit, submitCustomAnswer);
                return;
            }
            if(target.closest('[data-assistant-skip]')){
                runGuideAction(target.closest('[data-assistant-skip]'),skipQuestion);return;
            }
            if (target.closest('[data-assistant-custom-cancel]')) {
                showCustomInput(false);
                return;
            }
            if (target.closest('[data-assistant-back]') || target.closest('[data-assistant-back-to-guide]')) {
                if (guideState.state && guideState.state.stage === 'done') {
                    resetGuideState();
                    renderAll();
                    setStatus('状态：已返回助手查找。', 'info');
                } else {
                    runGuideAction(target.closest('button'), returnToPrevious);
                }
                return;
            }
            if (target.closest('[data-assistant-confirm]')) {
                confirmSelection();
                return;
            }
            if (target.closest('[data-assistant-fallback]')) {
                showFallbackControls();
                return;
            }
            if (target.closest('[data-assistant-fallback-submit]')) {
                requestFallback();
                return;
            }
            if (target.closest('[data-assistant-none]')) {
                openManual();
                return;
            }
            if (target.closest('[data-assistant-restart]')) {
                resetGuideState();
                renderAll();
                setStatus('状态：引导已重置。', 'info');
            }
            if (target.closest('[data-assistant-edit-answer]')) {
                editLastAnswer();
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
            if (target.matches('[data-assistant-backup-ack], [data-assistant-room-choice], [data-assistant-date-override], [data-assistant-period-override]')) {
                updateConfirmControls();
            }
            if (target.matches('[data-assistant-fallback-date], [data-assistant-fallback-teacher]')) {
                updateFallbackControls();
            }
        }

        function handleInput(event) {
            const target = event.target;
            if (!target) return;
            if (target.matches('[data-assistant-semester]')) {
                setSemesterError('');
                rememberSemester(target.value);
                updateFallbackControls();
            }
            if (target.matches('[data-assistant-fallback-date], [data-assistant-fallback-teacher]')) {
                updateFallbackControls();
            }
        }

        function handleFormContextChange(event) {
            const target = event && event.target;
            if (!target || !['lecture_date', 'start_period', 'end_period', 'class_period', 'lecture_location', 'teacher_name', 'course_title', 'student_grade_class'].includes(target.id)) {
                return;
            }
            if (!guideState.state) {
                guideState.formFactsDismissed = false;
                renderAll();
                return;
            }
            markAssistantStateStale();
        }

        root.addEventListener('click', handleClick);
        root.addEventListener('keydown', handleKeydown);
        root.addEventListener('change', handleChange);
        root.addEventListener('input', handleInput);
        form.addEventListener('change', handleFormContextChange);
        form.addEventListener('input', handleFormContextChange);
        form.addEventListener('reset', () => {
            resetGuideState();
            guideState.draftRestored = false;
            window.setTimeout(renderAll, 0);
        });
        form.addEventListener('submit', syncAssistantClearsBeforeSubmit, true);
        window.addEventListener('lecture-form-draft-loaded', handleDraftLoaded);
        hydrateRememberedSemester();
        if (elements.toggle) {
            elements.toggle.setAttribute('aria-expanded', 'true');
        }
        renderAll();
    }

    document.addEventListener('DOMContentLoaded', initListeningAssistant);
}());
