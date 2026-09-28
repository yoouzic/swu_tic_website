(function () {
    'use strict';

    const STATES = Object.freeze({
        FIND: 'find',
        RESCUE: 'rescue',
        REVIEW: 'review',
        MANUAL: 'manual',
        DONE: 'done',
        ERROR: 'error',
    });
    const TEMPLATE_VERSION = 'task6-v1';
    const SOURCE_LABELS = Object.freeze({
        primary: '当前权威课表',
        backup: '备用课表线索 · 需核对',
    });
    const ENDPOINTS = Object.freeze({
        candidates: '/user/api/listening-assistant/candidates',
        fallback: '/user/api/listening-assistant/fallback',
        confirm: '/user/api/listening-assistant/confirm',
    });
    const STATE_LABELS = Object.freeze({
        find: '查找',
        rescue: '救援',
        review: '复核',
        manual: '手动',
        done: '已完成',
        error: '错误',
    });
    const CONFLICT_LABELS = Object.freeze({
        period_mismatch: '查询节次与课表节次不一致，需核对。',
        venue_period_needs_confirmation: '场地节次需要人工核对。',
        date_needs_confirmation: '日期由星期或周次推断，需人工核对。',
    });

    function cleanText(value) {
        return value == null ? '' : String(value).trim();
    }

    function displayPeriod(value) {
        if (Array.isArray(value) && value.length === 2) {
            return value[0] === value[1] ? `第${value[0]}节` : `第${value[0]}-${value[1]}节`;
        }
        return cleanText(value) || '未提供';
    }

    function isObject(value) {
        return value !== null && typeof value === 'object' && !Array.isArray(value);
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
            candidates: root.querySelector('[data-assistant-candidates]'),
            sourceBatch: root.querySelector('[data-assistant-source-batch]'),
            rescueMessage: root.querySelector('[data-assistant-rescue-message]'),
            rescueReason: root.querySelector('[data-assistant-fallback-reason]'),
            rescueButton: root.querySelector('[data-assistant-rescue]'),
            review: root.querySelector('[data-assistant-review]'),
            overrideFields: root.querySelector('[data-assistant-override-fields]'),
            reviewHint: root.querySelector('[data-assistant-review-hint]'),
            backupAckWrap: root.querySelector('[data-assistant-backup-ack-wrap]'),
            backupAck: root.querySelector('[data-assistant-backup-ack]'),
            confirmButton: root.querySelector('[data-assistant-confirm]'),
            errorMessage: root.querySelector('[data-assistant-error-message]'),
            payload: form.querySelector('#assistant_payload'),
        };

        const state = {
            current: STATES.FIND,
            query: {},
            previousQuery: {},
            rejectedIds: [],
            candidates: [],
            selectedCandidate: null,
            primaryHadCandidates: false,
            backupSourceBatchId: '',
            backupRescueAvailable: false,
            fallbackReason: 'no_result',
            originalRoomForFallback: '',
            roomDecision: '',
            errorMessage: '',
            errorReturnState: STATES.FIND,
            requestId: 0,
            controller: null,
            assistantFilledFields: new Set(),
            assistantFilledGroups: new Set(),
        };

        function setStatus(message, tone) {
            if (!elements.status) {
                return;
            }
            elements.status.textContent = message;
            elements.status.dataset.tone = tone || 'info';
        }

        function setInlineMessage(message) {
            if (elements.rescueMessage) {
                elements.rescueMessage.textContent = message || '';
            }
        }

        function setState(nextState, message) {
            state.current = nextState;
            root.dataset.assistantState = nextState;
            if (elements.stateLabel) {
                elements.stateLabel.textContent = STATE_LABELS[nextState] || nextState;
            }
            root.querySelectorAll('[data-assistant-view]').forEach((view) => {
                view.hidden = view.dataset.assistantView !== nextState;
            });
            if (elements.candidates) {
                elements.candidates.hidden = ![STATES.FIND, STATES.RESCUE].includes(nextState);
            }
            if (message) {
                setStatus(message, nextState === STATES.ERROR ? 'error' : 'info');
            }
            updateRescueControls();
            updateReviewControls();
        }

        function invalidateRequests() {
            state.requestId += 1;
            if (state.controller) {
                state.controller.abort();
                state.controller = null;
            }
            return state.requestId;
        }

        function beginRequest() {
            const requestId = invalidateRequests();
            state.controller = new AbortController();
            return {requestId, signal: state.controller.signal};
        }

        function isCurrentRequest(requestId) {
            return requestId === state.requestId;
        }

        async function requestJson(url, options, request) {
            const headers = new Headers(options.headers || {});
            const csrfMeta = document.querySelector('meta[name="csrf-token"]');
            const csrfToken = csrfMeta ? csrfMeta.getAttribute('content') : '';
            headers.set('Accept', 'application/json');
            if (options.body) {
                headers.set('Content-Type', 'application/json');
            }
            if (csrfToken) {
                headers.set('X-CSRFToken', csrfToken);
            }
            const response = await fetch(url, {...options, headers, signal: request.signal});
            let payload;
            try {
                payload = await response.json();
            } catch (error) {
                throw new Error('服务器返回了无法读取的结果');
            }
            if (!response.ok || !payload || payload.success !== true) {
                throw new Error((payload && payload.message) || '听课助手请求失败');
            }
            return payload.data || {};
        }

        function readQuery() {
            const values = {
                date: cleanText(root.querySelector('#assistantDate')?.value),
                room: cleanText(root.querySelector('#assistantRoom')?.value),
                teacher: cleanText(root.querySelector('#assistantTeacher')?.value),
                period: cleanText(root.querySelector('#assistantPeriod')?.value),
                semester: cleanText(root.querySelector('#assistantSemester')?.value),
            };
            const query = {};
            Object.keys(values).forEach((key) => {
                if (values[key]) {
                    query[key] = values[key];
                }
            });
            return query;
        }

        function queryHasAnchor(query) {
            return Boolean(query.room || query.teacher);
        }

        function queryForConfirmation(query) {
            const result = {};
            if (query.date) {
                result.lecture_date = query.date;
            }
            if (query.room) {
                result.room = query.room;
            }
            if (query.teacher) {
                result.teacher_name = query.teacher;
            }
            if (query.period) {
                result.period = query.period;
            }
            if (query.semester) {
                result.semester = query.semester;
            }
            return result;
        }

        function buildCandidateQueryParams(query) {
            const params = new URLSearchParams();
            const queryMap = {date: 'date', room: 'room', teacher: 'teacher', period: 'period', semester: 'semester'};
            Object.keys(queryMap).forEach((key) => {
                if (query[key]) {
                    params.set(queryMap[key], query[key]);
                }
            });
            state.rejectedIds.forEach((candidateId) => params.append('rejected_ids', candidateId));
            return params;
        }

        function clearPayload() {
            if (elements.payload) {
                elements.payload.value = '';
            }
        }

        function clearSearchResults() {
            state.candidates = [];
            state.selectedCandidate = null;
            state.primaryHadCandidates = false;
            state.backupSourceBatchId = '';
            state.backupRescueAvailable = false;
            state.originalRoomForFallback = '';
            state.roomDecision = '';
            clearPayload();
            renderCandidates([]);
        }

        function handleQueryChange() {
            const nextQuery = readQuery();
            const changedKeys = Object.keys({...state.previousQuery, ...nextQuery}).filter(
                (key) => (state.previousQuery[key] || '') !== (nextQuery[key] || ''),
            );
            if (changedKeys.length && Object.keys(state.previousQuery).length) {
                invalidateRequests();
                state.rejectedIds = [];
                state.query = nextQuery;
                clearSearchResults();
                if (state.current !== STATES.FIND) {
                    setState(STATES.FIND, '查询条件已改变，请重新查找。');
                } else {
                    setStatus('状态：查找。查询条件已改变，请重新查找。', 'info');
                }
            }
            state.previousQuery = nextQuery;
            updateRescueControls();
        }

        function sourceLabel(candidate) {
            return candidate && candidate.source_kind === 'backup'
                ? SOURCE_LABELS.backup
                : SOURCE_LABELS.primary;
        }

        function conflictText(conflict) {
            return CONFLICT_LABELS[conflict] || `课表存在差异：${cleanText(conflict)}`;
        }

        function appendTextMeta(container, label, value) {
            const item = document.createElement('div');
            item.className = 'listening-assistant__meta-item';
            const labelElement = document.createElement('span');
            labelElement.className = 'listening-assistant__meta-label';
            labelElement.textContent = label;
            const valueElement = document.createElement('strong');
            valueElement.className = 'listening-assistant__meta-value';
            valueElement.textContent = cleanText(value) || '未提供';
            item.append(labelElement, valueElement);
            container.appendChild(item);
        }

        function createCandidateCard(candidate, index) {
            const candidateId = cleanText(candidate.candidate_id);
            if (!candidateId) {
                return null;
            }
            const card = document.createElement('article');
            card.className = 'listening-assistant__candidate-card';
            card.dataset.assistantCard = '';
            card.dataset.assistantCandidateId = candidateId;
            card.tabIndex = 0;
            const titleId = `listening-assistant-candidate-${index}`;
            const heading = document.createElement('h3');
            heading.className = 'listening-assistant__candidate-title';
            heading.id = titleId;
            heading.textContent = cleanText(candidate.course_title) || '未命名课程';
            card.setAttribute('aria-labelledby', titleId);

            const badges = document.createElement('div');
            badges.className = 'listening-assistant__badges';
            const sourceBadge = document.createElement('span');
            sourceBadge.className = 'badge listening-assistant__source-badge';
            sourceBadge.dataset.sourceKind = candidate.source_kind === 'backup' ? 'backup' : 'primary';
            sourceBadge.textContent = sourceLabel(candidate);
            badges.appendChild(sourceBadge);
            if (candidate.needs_confirmation === true) {
                const confirmationBadge = document.createElement('span');
                confirmationBadge.className = 'badge listening-assistant__confirmation-badge';
                confirmationBadge.textContent = '需确认';
                badges.appendChild(confirmationBadge);
            }

            const meta = document.createElement('div');
            meta.className = 'listening-assistant__meta-grid';
            appendTextMeta(meta, '教师', candidate.teacher_name);
            appendTextMeta(meta, '学院', candidate.teacher_college);
            appendTextMeta(meta, '教室', candidate.room);
            appendTextMeta(meta, '节次', displayPeriod(candidate.period));
            appendTextMeta(meta, '班级', candidate.student_grade_class);
            if (candidate.course_code || candidate.selection_code) {
                appendTextMeta(meta, '课程号/选课号', `${cleanText(candidate.course_code) || '-'} / ${cleanText(candidate.selection_code) || '-'}`);
            }

            const body = document.createElement('div');
            body.className = 'listening-assistant__candidate-body';
            body.append(heading, badges, meta);
            const conflicts = Array.isArray(candidate.conflicts) ? candidate.conflicts.filter(Boolean) : [];
            if (conflicts.length) {
                const warning = document.createElement('div');
                warning.className = 'listening-assistant__conflict-warning';
                warning.setAttribute('role', 'note');
                const warningTitle = document.createElement('strong');
                warningTitle.textContent = '需要核对：';
                const warningList = document.createElement('ul');
                conflicts.forEach((conflict) => {
                    const item = document.createElement('li');
                    item.textContent = conflictText(conflict);
                    warningList.appendChild(item);
                });
                warning.append(warningTitle, warningList);
                body.appendChild(warning);
            }

            const actions = document.createElement('div');
            actions.className = 'listening-assistant__candidate-actions';
            const selectButton = document.createElement('button');
            selectButton.type = 'button';
            selectButton.className = 'btn btn-primary';
            selectButton.dataset.assistantSelect = '';
            selectButton.dataset.assistantCandidateId = candidateId;
            selectButton.textContent = '选择此候选';
            const rejectButton = document.createElement('button');
            rejectButton.type = 'button';
            rejectButton.className = 'btn btn-outline-secondary';
            rejectButton.dataset.assistantReject = '';
            rejectButton.dataset.assistantCandidateId = candidateId;
            rejectButton.textContent = '排除';
            actions.append(selectButton, rejectButton);
            card.append(body, actions);
            return card;
        }

        function renderCandidates(candidates) {
            if (!elements.candidates) {
                return;
            }
            elements.candidates.replaceChildren();
            const validCandidates = Array.isArray(candidates) ? candidates.filter(isObject) : [];
            if (!validCandidates.length) {
                const empty = document.createElement('div');
                empty.className = 'listening-assistant__empty';
                empty.dataset.assistantCandidatesEmpty = '';
                empty.textContent = '暂无候选。你可以重新查找，或使用“都不是 / 手动填写”。';
                elements.candidates.appendChild(empty);
                return;
            }
            const heading = document.createElement('h3');
            heading.className = 'listening-assistant__candidate-list-title';
            heading.textContent = '请从候选中选择，不会默认选择第一项';
            elements.candidates.appendChild(heading);
            validCandidates.forEach((candidate, index) => {
                const card = createCandidateCard(candidate, index);
                if (card) {
                    elements.candidates.appendChild(card);
                }
            });
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

        function setError(message, returnState) {
            state.errorMessage = message || '听课助手暂时无法完成这一步。';
            state.errorReturnState = returnState || STATES.FIND;
            if (elements.errorMessage) {
                elements.errorMessage.textContent = state.errorMessage;
            }
            setState(STATES.ERROR, `状态：错误。${state.errorMessage}`);
        }

        async function searchCandidates() {
            const query = readQuery();
            if (!query.date) {
                setError('请先填写听课日期。', STATES.FIND);
                return;
            }
            if (!queryHasAnchor(query)) {
                setError('请填写教室或授课教师至少一项。', STATES.FIND);
                return;
            }
            state.query = query;
            state.previousQuery = {...query};
            state.selectedCandidate = null;
            state.originalRoomForFallback = '';
            state.roomDecision = '';
            state.fallbackReason = state.rejectedIds.length ? 'rejected_candidates' : 'no_result';
            renderCandidates([]);
            setState(STATES.FIND, '状态：查找。正在查询当前权威课表……');
            setBusy(root.querySelector('[data-assistant-search]'), true, '查询中…');
            const request = beginRequest();
            try {
                const params = buildCandidateQueryParams(query);
                const data = await requestJson(`${ENDPOINTS.candidates}?${params.toString()}`, {method: 'GET'}, request);
                if (!isCurrentRequest(request.requestId)) {
                    return;
                }
                state.candidates = Array.isArray(data.candidates) ? data.candidates : [];
                state.primaryHadCandidates = state.candidates.length > 0 || state.rejectedIds.length > 0;
                state.backupSourceBatchId = cleanText(data.backup_source_batch_id);
                state.backupRescueAvailable = data.backup_rescue_available === true && Boolean(state.backupSourceBatchId);
                state.fallbackReason = state.rejectedIds.length ? 'rejected_candidates' : 'no_result';
                renderCandidates(state.candidates);
                if (state.candidates.length) {
                    setState(STATES.FIND, `状态：查找。找到 ${state.candidates.length} 个候选，请选择或明确点选“都不是 / 手动填写”。`);
                } else {
                    setState(STATES.RESCUE, '状态：救援。当前权威课表没有可用候选。');
                }
            } catch (error) {
                if (error && error.name === 'AbortError') {
                    return;
                }
                if (isCurrentRequest(request.requestId)) {
                    setError(error.message || '当前课表查询失败，请稍后重试。', STATES.FIND);
                }
            } finally {
                setBusy(root.querySelector('[data-assistant-search]'), false);
                if (isCurrentRequest(request.requestId)) {
                    state.controller = null;
                }
            }
        }

        function updateRescueControls() {
            const query = state.query && Object.keys(state.query).length ? state.query : readQuery();
            const teacher = query.teacher || cleanText(form.querySelector('#teacher_name')?.value);
            const semester = query.semester;
            if (elements.sourceBatch) {
                elements.sourceBatch.textContent = state.backupSourceBatchId || '服务器尚未提供可用备用批次';
            }
            if (elements.rescueReason && state.fallbackReason) {
                elements.rescueReason.value = state.fallbackReason;
            }
            const ready = state.backupRescueAvailable
                && Boolean(state.backupSourceBatchId)
                && Boolean(query.date)
                && Boolean(teacher)
                && Boolean(semester);
            if (elements.rescueButton) {
                elements.rescueButton.disabled = !ready;
            }
            if (state.current === STATES.RESCUE) {
                if (!state.backupRescueAvailable || !state.backupSourceBatchId) {
                    setInlineMessage('当前权威课表没有返回可选择的备用来源，请手动填写。');
                } else if (!teacher) {
                    setInlineMessage('备用查询需要授课教师。请返回查找补充教师后再救援。');
                } else if (!semester) {
                    setInlineMessage('备用查询和最终确认需要课表学期，请返回查找补充学期。');
                } else {
                    setInlineMessage('备用来源已由服务器返回；点击查询后仍需人工核对并勾选来源确认。');
                }
            }
        }

        function renderReview() {
            if (!elements.review || !state.selectedCandidate) {
                return;
            }
            const candidate = state.selectedCandidate;
            elements.review.replaceChildren();
            const title = document.createElement('h3');
            title.className = 'listening-assistant__review-title';
            title.textContent = cleanText(candidate.course_title) || '未命名课程';
            const badges = document.createElement('div');
            badges.className = 'listening-assistant__badges';
            const sourceBadge = document.createElement('span');
            sourceBadge.className = 'badge listening-assistant__source-badge';
            sourceBadge.textContent = sourceLabel(candidate);
            badges.appendChild(sourceBadge);
            if (candidate.needs_confirmation === true) {
                const confirmationBadge = document.createElement('span');
                confirmationBadge.className = 'badge listening-assistant__confirmation-badge';
                confirmationBadge.textContent = '需确认';
                badges.appendChild(confirmationBadge);
            }
            const summary = document.createElement('div');
            summary.className = 'listening-assistant__review-summary';
            appendTextMeta(summary, '日期', candidate.lecture_date);
            appendTextMeta(summary, '教师', candidate.teacher_name);
            appendTextMeta(summary, '教室', candidate.room);
            appendTextMeta(summary, '节次', displayPeriod(candidate.period));
            appendTextMeta(summary, '班级', candidate.student_grade_class);
            elements.review.append(title, badges, summary);

            const conflicts = Array.isArray(candidate.conflicts) ? candidate.conflicts.filter(Boolean) : [];
            if (conflicts.length) {
                const warning = document.createElement('div');
                warning.className = 'listening-assistant__conflict-warning';
                warning.setAttribute('role', 'alert');
                const warningTitle = document.createElement('strong');
                warningTitle.textContent = '确认前请处理差异：';
                const warningList = document.createElement('ul');
                conflicts.forEach((conflict) => {
                    const item = document.createElement('li');
                    item.textContent = conflictText(conflict);
                    warningList.appendChild(item);
                });
                warning.append(warningTitle, warningList);
                elements.review.appendChild(warning);
            }

            const originalRoom = state.originalRoomForFallback;
            const candidateRoom = cleanText(candidate.room);
            if (originalRoom && candidateRoom && originalRoom !== candidateRoom) {
                const roomChoice = document.createElement('fieldset');
                roomChoice.className = 'listening-assistant__room-choice';
                const legend = document.createElement('legend');
                legend.className = 'form-label';
                legend.textContent = '备用课表返回了不同教室，请选择处理方式';
                roomChoice.appendChild(legend);
                [
                    ['candidate', `采用备用课表教室：${candidateRoom}`],
                    ['manual', `保留你输入的教室：${originalRoom}`],
                ].forEach(([value, labelText]) => {
                    const wrapper = document.createElement('div');
                    wrapper.className = 'form-check';
                    const input = document.createElement('input');
                    input.type = 'radio';
                    input.className = 'form-check-input';
                    input.id = `assistantRoomChoice-${value}`;
                    input.dataset.assistantRoomChoice = value;
                    input.checked = state.roomDecision === value;
                    const label = document.createElement('label');
                    label.className = 'form-check-label';
                    label.htmlFor = input.id;
                    label.textContent = labelText;
                    wrapper.append(input, label);
                    roomChoice.appendChild(wrapper);
                });
                elements.review.appendChild(roomChoice);
            }

            elements.overrideFields.replaceChildren();
            const needsPeriodOverride = conflicts.includes('period_mismatch') || conflicts.includes('venue_period_needs_confirmation');
            const needsDateOverride = conflicts.includes('date_needs_confirmation');
            const needsRoomOverride = conflicts.some((conflict) => String(conflict).toLowerCase().includes('location'));
            if (needsPeriodOverride || needsDateOverride || needsRoomOverride) {
                const titleElement = document.createElement('h4');
                titleElement.className = 'h6';
                titleElement.textContent = '人工核对值（必填项仅在存在差异时显示）';
                elements.overrideFields.appendChild(titleElement);
            }
            if (needsDateOverride) {
                appendOverrideField('确认日期', 'lecture_date', 'date', '请填写实际听课日期。');
            }
            if (needsPeriodOverride) {
                appendOverrideField('确认节次', 'period', 'text', '例如：第3-4节。');
            }
            if (needsRoomOverride) {
                appendOverrideField('确认教室', 'room', 'text', '请填写实际听课地点。');
            }
            if (candidate.source_kind === 'backup') {
                elements.backupAckWrap.hidden = false;
                elements.backupAck.checked = false;
            } else {
                elements.backupAckWrap.hidden = true;
                elements.backupAck.checked = false;
            }
            updateReviewControls();
        }

        function appendOverrideField(labelText, key, type, helpText) {
            const wrapper = document.createElement('div');
            wrapper.className = 'listening-assistant__field';
            const inputId = `assistantOverride-${key}`;
            const label = document.createElement('label');
            label.className = 'form-label';
            label.htmlFor = inputId;
            label.textContent = labelText;
            const input = document.createElement('input');
            input.type = type;
            input.className = 'form-control';
            input.id = inputId;
            input.dataset.assistantOverrideKey = key;
            input.setAttribute('aria-required', 'true');
            input.placeholder = helpText;
            wrapper.append(label, input);
            elements.overrideFields.appendChild(wrapper);
        }

        function updateReviewControls() {
            if (!elements.confirmButton || state.current !== STATES.REVIEW || !state.selectedCandidate) {
                return;
            }
            const missing = [];
            if (!state.query.semester) {
                missing.push('请返回查找补充课表学期');
            }
            if (state.selectedCandidate.source_kind === 'backup' && !elements.backupAck.checked) {
                missing.push('请勾选备用来源确认');
            }
            if (state.originalRoomForFallback && cleanText(state.selectedCandidate.room) && state.originalRoomForFallback !== cleanText(state.selectedCandidate.room) && !state.roomDecision) {
                missing.push('请选择教室处理方式');
            }
            elements.overrideFields.querySelectorAll('[data-assistant-override-key]').forEach((input) => {
                if (!cleanText(input.value)) {
                    const label = input.previousElementSibling ? input.previousElementSibling.textContent : '人工核对值';
                    missing.push(`请填写${label}`);
                }
            });
            elements.confirmButton.disabled = missing.length > 0;
            if (elements.reviewHint) {
                elements.reviewHint.textContent = missing.length ? missing.join('；') : '确认后服务器会重新核验候选，客户端显示的候选快照不会作为提交依据。';
            }
        }

        function collectOverrides() {
            const overrides = {};
            elements.overrideFields.querySelectorAll('[data-assistant-override-key]').forEach((input) => {
                const value = cleanText(input.value);
                if (value) {
                    overrides[input.dataset.assistantOverrideKey] = value;
                }
            });
            if (state.roomDecision === 'manual' && state.originalRoomForFallback) {
                overrides.lecture_location = state.originalRoomForFallback;
            }
            return overrides;
        }

        function buildConfirmationPayload() {
            const candidate = state.selectedCandidate;
            const sourceKind = candidate && candidate.source_kind === 'backup' ? 'backup' : 'primary';
            const payload = {
                query: queryForConfirmation(state.query),
                rejected_ids: state.rejectedIds.slice(),
                source_kind: sourceKind,
                candidate_id: cleanText(candidate && candidate.candidate_id),
                overrides: collectOverrides(),
                template_version: TEMPLATE_VERSION,
                stage: 'confirmed',
                acknowledged_source: sourceKind === 'backup' && elements.backupAck.checked,
                explicit_fallback: sourceKind === 'backup',
            };
            if (state.query.semester) {
                payload.semester = state.query.semester;
            }
            if (sourceKind === 'backup') {
                payload.source_batch_id = cleanText(state.backupSourceBatchId || (candidate && candidate.source_batch_id));
                payload.reason = state.fallbackReason;
                payload.fallback_reason = state.fallbackReason;
            }
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
            state.assistantFilledFields.add(id);
        }

        function applyFieldSnapshot(snapshot) {
            if (!isObject(snapshot)) {
                throw new Error('服务器没有返回可用的字段快照');
            }
            const changed = [];
            const dateInput = form.querySelector('#lecture_date');
            if (dateInput && fieldIsBlank(dateInput)) {
                setBlankField('lecture_date', snapshot.lecture_date, changed);
            }
            const startPeriod = form.querySelector('#start_period');
            const endPeriod = form.querySelector('#end_period');
            const classPeriod = form.querySelector('#class_period');
            if (fieldIsBlank(startPeriod) && fieldIsBlank(endPeriod) && fieldIsBlank(classPeriod) && snapshot.class_period) {
                const match = String(snapshot.class_period).match(/第(\d+)(?:-(\d+))?节/);
                if (match) {
                    startPeriod.value = match[1];
                    endPeriod.value = match[2] || match[1];
                    classPeriod.value = snapshot.class_period;
                    changed.push(startPeriod, endPeriod, classPeriod);
                    state.assistantFilledGroups.add('period');
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
                rejected_ids: state.rejectedIds.slice(),
                source_kind: cleanText(confirmation.source_kind || requestPayload.source_kind),
                candidate_id: cleanText(confirmation.candidate_id || requestPayload.candidate_id),
                overrides: isObject(responseData.overrides) ? responseData.overrides : requestPayload.overrides,
                template_version: cleanText(responseData.template_version || requestPayload.template_version),
                explicit_fallback: confirmation.explicit_fallback === true,
                acknowledged_source: confirmation.acknowledged_source === true,
            };
            const sourceBatchId = cleanText(confirmation.source_batch_id || requestPayload.source_batch_id);
            const semester = cleanText(confirmation.semester || requestPayload.semester || requestPayload.query.semester);
            const fallbackReason = cleanText(confirmation.fallback_reason || requestPayload.fallback_reason);
            if (sourceBatchId) {
                stored.source_batch_id = sourceBatchId;
            }
            if (semester) {
                stored.semester = semester;
            }
            if (fallbackReason) {
                stored.fallback_reason = fallbackReason;
            }
            elements.payload.value = JSON.stringify(stored);
        }

        function syncAssistantClearsBeforeSubmit() {
            if (!elements.payload || !cleanText(elements.payload.value)) {
                return;
            }
            let payload;
            try {
                payload = JSON.parse(elements.payload.value);
            } catch (error) {
                return;
            }
            if (!isObject(payload)) {
                return;
            }
            const overrides = isObject(payload.overrides) ? {...payload.overrides} : {};
            let changed = false;
            state.assistantFilledFields.forEach((fieldName) => {
                const field = form.querySelector(`#${fieldName}`);
                if (field && fieldIsBlank(field) && overrides[fieldName] !== null) {
                    overrides[fieldName] = null;
                    changed = true;
                    if (fieldName === 'lecture_date') {
                        const dateDisplay = form.querySelector('#lecture_date_display');
                        if (dateDisplay) {
                            dateDisplay.value = '';
                        }
                    }
                }
            });
            if (state.assistantFilledGroups.has('period')) {
                const startPeriod = form.querySelector('#start_period');
                const endPeriod = form.querySelector('#end_period');
                if (fieldIsBlank(startPeriod) && fieldIsBlank(endPeriod)) {
                    const classPeriod = form.querySelector('#class_period');
                    if (classPeriod) {
                        classPeriod.value = '';
                    }
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
            if (!state.selectedCandidate) {
                return;
            }
            updateReviewControls();
            if (elements.confirmButton.disabled) {
                setStatus('状态：复核。请先完成页面上标出的确认项。', 'error');
                return;
            }
            const requestPayload = buildConfirmationPayload();
            setBusy(elements.confirmButton, true, '确认中…');
            const request = beginRequest();
            try {
                const data = await requestJson(ENDPOINTS.confirm, {
                    method: 'POST',
                    body: JSON.stringify(requestPayload),
                }, request);
                if (!isCurrentRequest(request.requestId)) {
                    return;
                }
                applyFieldSnapshot(data.field_snapshot);
                storeAssistantPayload(data, requestPayload);
                setState(STATES.DONE, '状态：已完成。服务器已重新核验，空白基础字段已补充。');
            } catch (error) {
                if (error && error.name === 'AbortError') {
                    return;
                }
                if (isCurrentRequest(request.requestId)) {
                    setError(error.message || '确认失败，请重新选择并确认。', STATES.REVIEW);
                }
            } finally {
                setBusy(elements.confirmButton, false);
                if (isCurrentRequest(request.requestId)) {
                    state.controller = null;
                }
            }
        }

        async function rescueFallback() {
            const query = state.query && Object.keys(state.query).length ? state.query : readQuery();
            const teacher = query.teacher || cleanText(form.querySelector('#teacher_name')?.value);
            const semester = query.semester;
            const reason = cleanText(elements.rescueReason?.value) || state.fallbackReason;
            if (!query.date || !teacher || !semester || !state.backupSourceBatchId) {
                setError('备用查询需要日期、授课教师、课表学期，以及服务器返回的备用来源批次。', STATES.RESCUE);
                return;
            }
            state.query = {date: query.date, teacher, semester};
            state.fallbackReason = reason === 'rejected_candidates' ? 'rejected_candidates' : 'no_result';
            state.originalRoomForFallback = query.room || '';
            state.roomDecision = '';
            setState(STATES.RESCUE, '状态：救援。正在查询你明确选择的备用课表……');
            setBusy(elements.rescueButton, true, '查询中…');
            const request = beginRequest();
            try {
                const data = await requestJson(ENDPOINTS.fallback, {
                    method: 'POST',
                    body: JSON.stringify({
                        date: state.query.date,
                        teacher: state.query.teacher,
                        rejected_ids: state.rejectedIds.slice(),
                        reason: state.fallbackReason,
                        source_batch_id: state.backupSourceBatchId,
                        semester: state.query.semester,
                        explicit_fallback: true,
                    }),
                }, request);
                if (!isCurrentRequest(request.requestId)) {
                    return;
                }
                state.candidates = Array.isArray(data.candidates) ? data.candidates : [];
                state.backupSourceBatchId = cleanText(data.source_batch_id || state.backupSourceBatchId);
                state.backupRescueAvailable = Boolean(state.backupSourceBatchId);
                state.fallbackReason = cleanText(data.fallback_reason || state.fallbackReason);
                renderCandidates(state.candidates);
                if (state.candidates.length) {
                    setState(STATES.RESCUE, `状态：救援。备用课表返回 ${state.candidates.length} 个线索；请选择并核对来源。`);
                } else {
                    setState(STATES.RESCUE, '状态：救援。备用课表也没有返回候选，请手动填写。');
                }
            } catch (error) {
                if (error && error.name === 'AbortError') {
                    return;
                }
                if (isCurrentRequest(request.requestId)) {
                    setError(error.message || '备用课表查询失败，请手动填写。', STATES.RESCUE);
                }
            } finally {
                setBusy(elements.rescueButton, false);
                if (isCurrentRequest(request.requestId)) {
                    state.controller = null;
                }
            }
        }

        function selectCandidate(candidateId) {
            const candidate = state.candidates.find((item) => cleanText(item.candidate_id) === candidateId);
            if (!candidate) {
                setError('候选已失效，请重新查询。', state.current === STATES.RESCUE ? STATES.RESCUE : STATES.FIND);
                return;
            }
            state.selectedCandidate = candidate;
            state.roomDecision = '';
            clearPayload();
            renderReview();
            setState(STATES.REVIEW, '状态：复核。请核对课程、来源和所有差异后确认。');
            root.querySelector('[data-assistant-confirm]')?.focus();
        }

        function rejectCandidate(candidateId) {
            if (!state.rejectedIds.includes(candidateId)) {
                state.rejectedIds.push(candidateId);
            }
            state.candidates = state.candidates.filter((item) => cleanText(item.candidate_id) !== candidateId);
            state.fallbackReason = 'rejected_candidates';
            renderCandidates(state.candidates);
            if (!state.candidates.length) {
                setState(STATES.RESCUE, '状态：救援。候选已排除，请决定是否查询备用课表。');
            } else {
                setStatus(`状态：查找。已排除 1 个候选，还剩 ${state.candidates.length} 个；“都不是 / 手动填写”仍可用。`, 'info');
            }
            updateRescueControls();
        }

        function openManual() {
            invalidateRequests();
            state.selectedCandidate = null;
            clearPayload();
            setState(STATES.MANUAL, '状态：手动。助手已停止，不会提交表单；请继续使用下方原有字段。');
        }

        function returnToFind() {
            invalidateRequests();
            state.selectedCandidate = null;
            state.roomDecision = '';
            setState(STATES.FIND, '状态：查找。可以重新输入条件或继续手动填写。');
            root.querySelector('#assistantDate')?.focus();
        }

        function openRescue() {
            invalidateRequests();
            state.fallbackReason = state.primaryHadCandidates || state.rejectedIds.length ? 'rejected_candidates' : 'no_result';
            if (elements.rescueReason) {
                elements.rescueReason.value = state.fallbackReason;
            }
            setState(STATES.RESCUE, '状态：救援。请先确认即将查询的备用来源。');
            elements.rescueButton?.focus();
        }

        function handleClick(event) {
            const target = event.target instanceof Element ? event.target : null;
            if (!target || !root.contains(target)) {
                return;
            }
            if (target.closest('[data-assistant-toggle]')) {
                const expanded = elements.toggle.getAttribute('aria-expanded') === 'true';
                elements.toggle.setAttribute('aria-expanded', String(!expanded));
                elements.panel.hidden = expanded;
                elements.toggleHint.textContent = expanded ? '展开' : '收起';
                return;
            }
            if (target.closest('[data-assistant-search]')) {
                searchCandidates();
                return;
            }
            if (target.closest('[data-assistant-none]')) {
                openRescue();
                return;
            }
            if (target.closest('[data-assistant-rescue]')) {
                rescueFallback();
                return;
            }
            if (target.closest('[data-assistant-manual]')) {
                openManual();
                return;
            }
            if (target.closest('[data-assistant-find-again]')) {
                returnToFind();
                return;
            }
            if (target.closest('[data-assistant-confirm]')) {
                confirmSelection();
                return;
            }
            if (target.closest('[data-assistant-back]')) {
                setState(state.selectedCandidate && state.selectedCandidate.source_kind === 'backup' ? STATES.RESCUE : STATES.FIND, '状态：查找。请重新选择候选。');
                return;
            }
            if (target.closest('[data-assistant-retry]')) {
                if (state.errorReturnState === STATES.REVIEW && state.selectedCandidate) {
                    renderReview();
                    setState(STATES.REVIEW, '状态：复核。请修正后重试。');
                } else if (state.errorReturnState === STATES.RESCUE) {
                    setState(STATES.RESCUE, '状态：救援。请确认备用来源后重试。');
                } else {
                    searchCandidates();
                }
                return;
            }
            const selectButton = target.closest('[data-assistant-select]');
            if (selectButton) {
                selectCandidate(cleanText(selectButton.dataset.assistantCandidateId));
                return;
            }
            const rejectButton = target.closest('[data-assistant-reject]');
            if (rejectButton) {
                rejectCandidate(cleanText(rejectButton.dataset.assistantCandidateId));
            }
        }

        function handleKeydown(event) {
            const target = event.target instanceof Element ? event.target : null;
            const card = target && target.closest('[data-assistant-card]');
            if (!card || target !== card || (event.key !== 'Enter' && event.key !== ' ')) {
                return;
            }
            event.preventDefault();
            selectCandidate(cleanText(card.dataset.assistantCandidateId));
        }

        function handleInput(event) {
            const target = event.target;
            if (target && target.matches && target.matches('[data-assistant-query]')) {
                handleQueryChange();
            }
            if (target && target.matches && target.matches('[data-assistant-override-key]')) {
                updateReviewControls();
            }
        }

        function handleChange(event) {
            const target = event.target;
            if (target && target.matches && target.matches('[data-assistant-query]')) {
                handleQueryChange();
            }
            if (target && target.matches && target.matches('[data-assistant-room-choice]')) {
                state.roomDecision = target.dataset.assistantRoomChoice || '';
                updateReviewControls();
            }
            if (target && target.matches && target.matches('[data-assistant-backup-ack]')) {
                updateReviewControls();
            }
            if (target && target.matches && target.matches('[data-assistant-override-key]')) {
                updateReviewControls();
            }
        }

        root.addEventListener('click', handleClick);
        root.addEventListener('keydown', handleKeydown);
        root.addEventListener('input', handleInput);
        root.addEventListener('change', handleChange);
        form.addEventListener('submit', syncAssistantClearsBeforeSubmit, true);
        root.querySelector('[data-assistant-fallback-reason]')?.addEventListener('change', () => {
            state.fallbackReason = elements.rescueReason.value === 'rejected_candidates' ? 'rejected_candidates' : 'no_result';
        });
        setState(STATES.FIND);
        renderCandidates([]);
    }

    document.addEventListener('DOMContentLoaded', initListeningAssistant);
}());
