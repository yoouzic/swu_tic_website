(function () {
    'use strict';
    const ISSUE_FIELDS = ['course_changes', 'abnormal_situation', 'suggestions'];
    const RATINGS = ['classroom_discipline', 'classroom_atmosphere', 'courseware_quality', 'overall_effect'];
    const METHODS = ['tm_ppt', 'tm_lecture', 'tm_interactive'];
    const METHOD_VALUES = 'PPT演示法、讲授法、师生互动法';

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
            METHODS.forEach(id => writePreset(form, id, true, owned));
            writePreset(form, 'teaching_method', METHOD_VALUES, owned);
            RATINGS.forEach(id => writePreset(form, id, '非常好', owned));
            writePreset(form, 'quality_case', '推荐', owned);
        } else {
            restorePreset(form, [...METHODS, 'teaching_method', ...RATINGS, 'quality_case'], owned);
            if (answer === 'no') writePreset(form, 'quality_case', '不推荐', owned);
        }
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
        const recommendationQuestion = panel.querySelector('[data-completion-recommendation]');
        const summary = panel.querySelector('[data-completion-summary]');
        const checklist = panel.querySelector('[data-completion-checklist]');
        const list = panel.querySelector('[data-completion-missing]');
        const count = panel.querySelector('[data-completion-count]');
        const owned = new Map();
        let active = false;
        let applying = false;
        let captureId = null;
        const answers = {issues: null, recommendation: null};

        function missingFields() {
            const fields = Array.from(form.querySelectorAll('[required]'))
                .filter(field => field.type !== 'hidden' && !panel.contains(field) && needsAttention(field));
            if (!METHODS.some(id => form.querySelector(`#${id}`)?.checked)) fields.push(form.querySelector('#tm_ppt'));
            if (!form.querySelector('#class_period')?.value && !fields.some(field => field.id === 'start_period')) fields.push(form.querySelector('#start_period'));
            return fields.filter(Boolean);
        }

        function renderChecklist() {
            const visible = active && answers.issues !== null && answers.recommendation !== null;
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
            issueQuestion.hidden = answers.issues !== null;
            recommendationQuestion.hidden = answers.issues === null || answers.recommendation !== null;
            summary.hidden = answers.issues === null || answers.recommendation === null;
            renderChecklist();
        }

        function activate(restored) {
            active = true;
            if (restored) {
                const issueValues = ISSUE_FIELDS.map(id => form.querySelector(`#${id}`)?.value.trim() || '');
                answers.issues = issueValues.every(value => value === '无') ? 'none' : (issueValues.every(Boolean) ? 'some' : null);
                const recommended = form.querySelector('#quality_case')?.value === '推荐'
                    && METHODS.every(id => form.querySelector(`#${id}`)?.checked)
                    && RATINGS.every(id => form.querySelector(`#${id}`)?.value === '非常好');
                answers.recommendation = recommended ? 'yes' : (form.querySelector('#quality_case')?.value === '不推荐' ? 'no' : null);
            } else {
                answers.issues = null;
                answers.recommendation = null;
            }
            render();
        }

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
            } else if (button.hasAttribute('data-completion-recommend-answer')) {
                answers.recommendation = button.dataset.completionRecommendAnswer;
                applyRecommendation(form, answers.recommendation, owned);
            } else if (button.hasAttribute('data-completion-edit')) {
                answers.issues = null;
                answers.recommendation = null;
            } else if(button.hasAttribute('data-completion-back')) {
                answers.issues=null;
            }
            applying = false;
            const hint = form.querySelector('[data-completion-recommendation-hint]');
            if (hint && form.querySelector('#quality_case')?.value) hint.hidden = true;
            render();
            const nextQuestion = answers.issues === null ? issueQuestion : recommendationQuestion;
            if (!nextQuestion.hidden) nextQuestion.querySelector('button')?.focus({preventScroll: true});
        });
        function onFormEdit(event) {
            if (applying) return;
            const field = event?.target;
            if (field?.matches('.form-control, .form-select') && !needsAttention(field)) field.classList.remove('is-invalid');
            applying = true;
            const invalidated = invalidateRecommendation(form);
            applying = false;
            if (invalidated) {
                answers.recommendation = 'skip';
                const hint = form.querySelector('[data-completion-recommendation-hint]');
                if (hint) hint.hidden = false;
            }
            const hint = form.querySelector('[data-completion-recommendation-hint]');
            if (hint && form.querySelector('#quality_case')?.value) hint.hidden = true;
            renderChecklist();
        }
        form.addEventListener('input', onFormEdit);
        form.addEventListener('change', onFormEdit);
        form.addEventListener('lecture-assistant-course-confirmed', () => activate(false));
        form.addEventListener('lecture-assistant-course-cleared', () => {
            active = false;
            answers.issues = null;
            answers.recommendation = null;
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
                answers.issues=null;
                answers.recommendation=null;
                render();
            }
        });
        form.addEventListener('reset', () => {
            active = false;
            answers.issues = null;
            answers.recommendation = null;
            captureId = null;
            owned.clear();
            render();
            window.setTimeout(() => {
                if (typeof syncCoursewareQualityWithTeachingMethod === 'function') syncCoursewareQualityWithTeachingMethod();
                if (typeof updateDateWithWeekday === 'function') updateDateWithWeekday();
                if (typeof updateClassPeriod === 'function') updateClassPeriod();
                const hint = form.querySelector('[data-completion-recommendation-hint]');
                if (hint) hint.hidden = true;
            }, 0);
        });
        render();
    }
    function invalidateRecommendation(form) {
        const field = form.querySelector('#quality_case');
        if (field?.value !== '推荐') return false;
        const eligible = METHODS.every(id => form.querySelector(`#${id}`)?.checked)
            && RATINGS.every(id => form.querySelector(`#${id}`)?.value === '非常好');
        if (eligible) return false;
        field.value = '';
        notify(field);
        return true;
    }
    if (typeof module !== 'undefined') module.exports = {applyIssues, applyRecommendation, needsAttention, invalidateRecommendation};
    if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', init);
}());
