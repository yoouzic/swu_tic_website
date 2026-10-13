(function () {
    'use strict';
    const ISSUE_FIELDS = ['course_changes', 'abnormal_situation', 'suggestions'];
    const METHODS = ['tm_ppt', 'tm_lecture', 'tm_interactive'];
    const METHOD_LABELS = {tm_ppt: 'PPT演示法', tm_lecture: '讲授法', tm_interactive: '师生互动法'};
    const RATINGS = ['classroom_discipline', 'classroom_atmosphere', 'courseware_quality', 'overall_effect'];
    const RATING_LABELS = {classroom_discipline: '管理课堂纪律', classroom_atmosphere: '调动课堂气氛', courseware_quality: '课件制作质量', overall_effect: '整体教学效果'};

    function applicableRatings(form) {
        return RATINGS.filter(id => id !== 'courseware_quality' || form.querySelector('#tm_ppt')?.checked);
    }

    function notify(field) {
        field.dispatchEvent(new Event('input', {bubbles: true}));
        field.dispatchEvent(new Event('change', {bubbles: true}));
    }

    function writePreset(form, id, value, owned) {
        const field = form.querySelector(`#${id}`);
        if (!field) return;
        const property = field.type === 'checkbox' ? 'checked' : 'value';
        if (!owned.has(id) || field[property] !== owned.get(id).value) owned.set(id, {before: field[property], value});
        else owned.get(id).value = value;
        field[property] = value;
        notify(field);
    }

    function restorePreset(form, ids, owned) {
        ids.forEach(id => {
            const field = form.querySelector(`#${id}`);
            const previous = owned.get(id);
            if (!field || !previous) return;
            const property = field.type === 'checkbox' ? 'checked' : 'value';
            if (field[property] === previous.value) {
                field[property] = previous.before;
                notify(field);
            }
            owned.delete(id);
        });
    }

    function applyIssues(form, answer, owned) {
        if(answer==='skip'){restorePreset(form,ISSUE_FIELDS,owned);return;}
        if (answer === 'none') ISSUE_FIELDS.forEach(id => writePreset(form, id, '无', owned));
        else {
            restorePreset(form, ISSUE_FIELDS, owned);
            ISSUE_FIELDS.forEach(id => {
                const field = form.querySelector(`#${id}`);
                if (field?.value.trim() === '无') { field.value = ''; notify(field); }
            });
        }
    }

    function applyRecommendation(form, answer, owned) {
        if (answer === 'yes') {
            writePreset(form, 'quality_case', '推荐', owned);
        } else {
            restorePreset(form, ['quality_case'], owned);
            if (answer === 'no') writePreset(form, 'quality_case', '不推荐', owned);
        }
    }

    function applyTeachingMethods(form, selected, owned) {
        const chosen = METHODS.filter(id => selected.includes(id));
        if (!chosen.length) return false;
        if (!chosen.includes('tm_ppt')) writePreset(form, 'courseware_quality', '无', owned);
        METHODS.forEach(id => writePreset(form, id, chosen.includes(id), owned));
        writePreset(form, 'teaching_method', chosen.map(id => METHOD_LABELS[id]).join('、'), owned);
        return true;
    }

    function applyRatingComplement(form, selected, owned) {
        if (!form.querySelector('#tm_ppt')?.checked) writePreset(form, 'courseware_quality', '无', owned);
        const applicable = applicableRatings(form);
        applicable.forEach(id => {
            if (!selected.includes(id)) writePreset(form, id, '非常好', owned);
            else {
                restorePreset(form, [id], owned);
                if (form.querySelector(`#${id}`)?.value === '非常好') writePreset(form, id, '', owned);
            }
        });
        return applicable.filter(id => selected.includes(id));
    }

    function needsAttention(field) {
        if (!field || field.disabled) return false;
        const value = String(field.value || '').trim();
        if (!value) return true;
        if (field.id === 'course_feedback' && value.length < 50) return true;
        if (/^contact_phone[12]$/.test(field.id) && !/^\d{11}$/.test(value)) return true;
        return Boolean(field.validity && !field.validity.valid);
    }

    function init() {
        const form = document.getElementById('lectureForm');
        const panel = document.querySelector('[data-form-completion]');
        if (!form || !panel) return;
        const issueQuestion = panel.querySelector('[data-completion-issues]');
        const methodQuestion = panel.querySelector('[data-completion-methods]');
        const ratingQuestion = panel.querySelector('[data-completion-ratings]');
        const ratingDetail = panel.querySelector('[data-completion-rating-detail]');
        const ratingTitle = panel.querySelector('[data-completion-rating-title]');
        const ratingProgress = panel.querySelector('[data-completion-rating-progress]');
        const methodChoices = Array.from(panel.querySelectorAll('[data-completion-method]'));
        const ratingChoices = Array.from(panel.querySelectorAll('[data-completion-rating]'));
        const recommendationQuestion = panel.querySelector('[data-completion-recommendation]');
        const summary = panel.querySelector('[data-completion-summary]');
        const checklist = panel.querySelector('[data-completion-checklist]');
        const list = panel.querySelector('[data-completion-missing]');
        const count = panel.querySelector('[data-completion-count]');
        const owned = new Map();
        let active = false;
        let applying = false;
        let captureId = null;
        const answers = {issues: null, methods: null, ratings: null, recommendation: null};
        let ratingQueue = [];
        let ratingIndex = 0;

        function resetAnswers() {
            Object.keys(answers).forEach(key => { answers[key] = null; });
            ratingQueue = [];
            ratingIndex = 0;
        }

        function syncMethodChoices() {
            methodChoices.forEach(choice => { choice.checked = Boolean(form.querySelector(`#${choice.dataset.completionMethod}`)?.checked); });
        }

        function syncRatingChoices() {
            const applicable = applicableRatings(form);
            const hasValues = applicable.some(id => form.querySelector(`#${id}`)?.value);
            ratingChoices.forEach(choice => {
                const id = choice.dataset.completionRating;
                choice.checked = applicable.includes(id) && hasValues && form.querySelector(`#${id}`)?.value !== '非常好';
            });
        }

        function ratingsComplete() {
            return applicableRatings(form).every(id => ['非常好', '好', '一般'].includes(form.querySelector(`#${id}`)?.value));
        }

        function stage() {
            if (answers.issues === null) return 'issues';
            if (answers.methods === null) return 'methods';
            if (answers.ratings === null) return 'ratings';
            if (answers.ratings === 'details') return 'rating-detail';
            if (answers.recommendation === null) return 'recommendation';
            return 'summary';
        }

        function missingFields() {
            const fields = Array.from(form.querySelectorAll('[required]'))
                .filter(field => field.type !== 'hidden' && !panel.contains(field) && needsAttention(field));
            if (!METHODS.some(id => form.querySelector(`#${id}`)?.checked)) fields.push(form.querySelector('#tm_ppt'));
            if (!form.querySelector('#class_period')?.value && !fields.some(field => field.id === 'start_period')) fields.push(form.querySelector('#start_period'));
            return fields.filter(Boolean);
        }

        function renderChecklist() {
            const visible = active && stage() === 'summary';
            checklist.hidden = !visible;
            const missing = visible ? missingFields() : [];
            const pending = new Set(missing.map(field => field.id));
            form.querySelectorAll('.form-control, .form-select, .form-check-input').forEach(field => {
                field.classList.toggle('form-assistant-pending', pending.has(field.id));
            });
            form.querySelectorAll('[data-completion-field-hint]').forEach(hint => hint.remove());
            list.replaceChildren();
            if (!visible) return;
            count.textContent = missing.length ? `待补充（${missing.length}项）` : '必填信息已补齐，请核对后提交';
            missing.forEach(field => {
                const label = form.querySelector(`label[for="${field.id}"]`) || field.closest('.mb-3')?.querySelector('label');
                let title = field.id === 'tm_ppt' ? '主要教学方法' : String(label?.textContent || field.name || field.id).replace(/\s*\*\s*/g, '').trim();
                if (field.id === 'course_feedback') title += '（至少50字）';
                if (/^contact_phone[12]$/.test(field.id)) title += '（11位手机号）';
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'btn btn-outline-secondary';
                button.dataset.completionTarget = field.id;
                button.textContent = title;
                list.appendChild(button);
                if (label) {
                    const hint = document.createElement('span');
                    hint.dataset.completionFieldHint = '';
                    hint.className = 'form-assistant-field-hint';
                    hint.textContent = '待补充';
                    label.appendChild(hint);
                }
            });
        }

        function render() {
            panel.hidden = !active;
            const current = stage();
            issueQuestion.hidden = current !== 'issues';
            methodQuestion.hidden = current !== 'methods';
            ratingQuestion.hidden = current !== 'ratings';
            ratingDetail.hidden = current !== 'rating-detail';
            recommendationQuestion.hidden = current !== 'recommendation';
            summary.hidden = current !== 'summary';
            panel.querySelector('[data-completion-method-confirm]').disabled = !methodChoices.some(choice => choice.checked);
            const usesCourseware = Boolean(form.querySelector('#tm_ppt')?.checked);
            panel.querySelector('[data-completion-courseware-choice]').hidden = !usesCourseware;
            const noCourseware = panel.querySelector('[data-completion-no-courseware]');
            if (noCourseware) noCourseware.hidden = usesCourseware;
            ratingChoices.forEach(choice => {
                if (choice.dataset.completionRating === 'courseware_quality') {
                    choice.disabled = !usesCourseware;
                    if (!usesCourseware) choice.checked = false;
                }
            });
            const selectedCount = ratingChoices.filter(choice => choice.checked && !choice.disabled).length;
            panel.querySelector('[data-completion-rating-confirm]').textContent = selectedCount ? '确认选择，逐项评价' : '均为非常好，继续';
            if (current === 'rating-detail') {
                ratingTitle.textContent = RATING_LABELS[ratingQueue[ratingIndex]];
                ratingProgress.textContent = `第${ratingIndex + 1}项，共${ratingQueue.length}项`;
            }
            renderChecklist();
        }

        function activate(restored) {
            active = true;
            if (restored) {
                const issueValues = ISSUE_FIELDS.map(id => form.querySelector(`#${id}`)?.value.trim() || '');
                answers.issues = issueValues.every(value => value === '无') ? 'none' : (issueValues.every(Boolean) ? 'some' : null);
                const recommended = form.querySelector('#quality_case')?.value === '推荐';
                answers.recommendation = recommended ? 'yes' : (form.querySelector('#quality_case')?.value === '不推荐' ? 'no' : null);
                answers.methods = METHODS.some(id => form.querySelector(`#${id}`)?.checked) ? 'done' : null;
                answers.ratings = ratingsComplete() ? 'done' : null;
            } else {
                resetAnswers();
            }
            syncMethodChoices();
            syncRatingChoices();
            render();
        }

        panel.addEventListener('change', () => render());

        panel.addEventListener('click', event => {
            const button = event.target.closest('button');
            if (!button) return;
            if (button.dataset.completionTarget) {
                const field = form.querySelector(`#${button.dataset.completionTarget}`);
                field?.scrollIntoView({behavior: 'smooth', block: 'center'});
                field?.focus({preventScroll: true});
                return;
            }
            applying = true;
            if (button.hasAttribute('data-completion-issue-answer')) {
                answers.issues = button.dataset.completionIssueAnswer;
                applyIssues(form, answers.issues, owned);
            } else if (button.hasAttribute('data-completion-method-confirm')) {
                const chosen = methodChoices.filter(choice => choice.checked).map(choice => choice.dataset.completionMethod);
                if (applyTeachingMethods(form, chosen, owned)) {
                    answers.methods = 'done';
                    answers.ratings = null;
                    syncRatingChoices();
                }
            } else if (button.hasAttribute('data-completion-method-skip')) {
                answers.methods = 'skip';
                answers.ratings = 'skip';
            } else if (button.hasAttribute('data-completion-rating-confirm')) {
                const chosen = ratingChoices.filter(choice => choice.checked && !choice.disabled).map(choice => choice.dataset.completionRating);
                ratingQueue = applyRatingComplement(form, chosen, owned);
                ratingIndex = 0;
                answers.ratings = ratingQueue.length ? 'details' : 'done';
            } else if (button.hasAttribute('data-completion-rating-answer')) {
                const value = button.dataset.completionRatingAnswer;
                if (answers.ratings === 'details' && ['好', '一般'].includes(value)) {
                    writePreset(form, ratingQueue[ratingIndex], value, owned);
                    ratingIndex += 1;
                    if (ratingIndex >= ratingQueue.length) answers.ratings = 'done';
                }
            } else if (button.hasAttribute('data-completion-rating-skip')) {
                restorePreset(form, applicableRatings(form), owned);
                answers.ratings = 'skip';
            } else if (button.hasAttribute('data-completion-go-back')) {
                const destination = button.dataset.completionGoBack;
                if (destination === 'issues') answers.issues = null;
                if (destination === 'methods') { answers.methods = null; syncMethodChoices(); }
                if (destination === 'ratings') { answers.ratings = null; syncRatingChoices(); }
            } else if (button.hasAttribute('data-completion-recommend-answer')) {
                answers.recommendation = button.dataset.completionRecommendAnswer;
                applyRecommendation(form, answers.recommendation, owned);
            } else if (button.hasAttribute('data-completion-edit')) {
                resetAnswers();
                syncMethodChoices();
                syncRatingChoices();
            } else if(button.hasAttribute('data-completion-back')) {
                answers.ratings = null;
                syncRatingChoices();
            }
            applying = false;
            render();
            const nextQuestion = {issues: issueQuestion, methods: methodQuestion, ratings: ratingQuestion,
                'rating-detail': ratingDetail, recommendation: recommendationQuestion, summary}[stage()];
            if (!nextQuestion.hidden) nextQuestion.querySelector('button')?.focus({preventScroll: true});
        });
        function onFormEdit(event) {
            if (applying) return;
            const field = event?.target;
            if (field && panel.contains(field)) return;
            if (field?.matches('.form-control, .form-select') && !needsAttention(field)) field.classList.remove('is-invalid');
            if (active && METHODS.includes(field?.id)) {
                if (!METHODS.some(id => form.querySelector(`#${id}`)?.checked)) answers.methods = null;
                if (answers.ratings !== null && !ratingsComplete()) { answers.ratings = null; syncRatingChoices(); }
                syncMethodChoices();
            }
            if (active && field?.id === 'quality_case') {
                answers.recommendation = field.value === '推荐' ? 'yes' : (field.value === '不推荐' ? 'no' : null);
            }
            render();
        }
        form.addEventListener('input', onFormEdit);
        form.addEventListener('change', onFormEdit);
        form.addEventListener('lecture-assistant-course-confirmed', () => activate(false));
        form.addEventListener('lecture-assistant-course-cleared', () => {
            active = false;
            resetAnswers();
            applying = true;
            restorePreset(form, Array.from(owned.keys()), owned);
            applying = false;
            render();
        });
        window.addEventListener('lecture-form-draft-loaded', event => {
            const data=event.detail?.data;
            const nextCaptureId=data?.site_capture_id==null?null:String(data.site_capture_id);
            if(captureId!==nextCaptureId)owned.clear();
            captureId=nextCaptureId;
            if (data?.assistant?.stage === 'confirmed' && form.querySelector('#assistant_payload')?.value) activate(true);
            else {
                active=false;
                resetAnswers();
                syncMethodChoices();
                syncRatingChoices();
                render();
            }
        });
        form.addEventListener('reset', () => {
            active = false;
            resetAnswers();
            captureId = null;
            owned.clear();
            render();
            window.setTimeout(() => {
                if (typeof syncCoursewareQualityWithTeachingMethod === 'function') syncCoursewareQualityWithTeachingMethod();
                if (typeof updateDateWithWeekday === 'function') updateDateWithWeekday();
                if (typeof updateClassPeriod === 'function') updateClassPeriod();
            }, 0);
        });
        render();
    }
    if (typeof module !== 'undefined') module.exports = {applyIssues, applyRecommendation, applyTeachingMethods, applyRatingComplement, needsAttention};
    if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', init);
}());
