(function () {
    'use strict';

    const root = document.getElementById('courseLookup');
    const form = document.getElementById('courseLookupForm');
    if (!root || !form) {
        return;
    }
    const status = document.getElementById('lookupStatus');
    const results = document.getElementById('lookupResults');
    const button = document.getElementById('lookupSubmit');
    const conflictLabels = {
        date_needs_confirmation: '上课日期需要核实',
        period_mismatch: '节次与查询条件不一致',
        venue_period_needs_confirmation: '场地节次需要核实',
        week_mismatch: '教学周需要核实',
        invalid_week_data: '教学周数据需要核实'
    };

    function showStatus(message, tone) {
        status.className = 'alert alert-' + tone;
        status.textContent = message;
    }

    function addDetail(list, label, value) {
        const row = document.createElement('div');
        row.className = 'd-flex flex-wrap gap-2 mb-1';
        const term = document.createElement('dt');
        term.className = 'text-muted fw-normal';
        term.textContent = label;
        const detail = document.createElement('dd');
        detail.className = 'mb-0 text-break';
        detail.textContent = value || '-';
        row.append(term, detail);
        list.appendChild(row);
    }

    function renderCandidate(candidate) {
        const column = document.createElement('div');
        column.className = 'col-12 col-lg-6';
        const card = document.createElement('article');
        card.className = 'card h-100';
        const body = document.createElement('div');
        body.className = 'card-body';
        const title = document.createElement('h2');
        title.className = 'h5 text-break';
        title.textContent = candidate.course_title || '未命名课程';
        const list = document.createElement('dl');
        list.className = 'mb-0';
        const period = Array.isArray(candidate.period) ? candidate.period.join('-') : '';
        addDetail(list, '教师', [candidate.teacher_name, candidate.teacher_college].filter(Boolean).join(' · '));
        addDetail(list, '时间', [candidate.lecture_date, period ? period + ' 节' : ''].filter(Boolean).join(' · '));
        addDetail(list, '教室', candidate.room);
        addDetail(list, '班级', candidate.student_grade_class);
        addDetail(list, '课程号 / 选课号', [candidate.course_code, candidate.selection_code].filter(Boolean).join(' / '));
        body.append(title, list);
        const conflicts = Array.isArray(candidate.conflicts) ? candidate.conflicts : [];
        if (conflicts.length || candidate.needs_confirmation) {
            const notice = document.createElement('p');
            notice.className = 'alert alert-warning mt-3 mb-0';
            notice.textContent = conflicts.length
                ? '待核实：' + conflicts.map((code) => conflictLabels[code] || '课程信息需要核实').join('；')
                : '课程信息需要人工核实。';
            body.appendChild(notice);
        }
        card.appendChild(body);
        column.appendChild(card);
        results.appendChild(column);
    }

    form.addEventListener('submit', async function (event) {
        event.preventDefault();
        const date = document.getElementById('lookupDate').value;
        const room = document.getElementById('lookupRoom').value.trim();
        const teacher = document.getElementById('lookupTeacher').value.trim();
        results.replaceChildren();
        if (!date || (!room && !teacher)) {
            showStatus('请选择日期，并填写教室或教师。', 'warning');
            return;
        }
        const url = new URL(root.dataset.candidatesUrl, window.location.origin);
        url.searchParams.set('date', date);
        if (room) {
            url.searchParams.set('room', room);
        }
        if (teacher) {
            url.searchParams.set('teacher', teacher);
        }
        button.disabled = true;
        results.setAttribute('aria-busy', 'true');
        showStatus('正在查询课程…', 'secondary');
        try {
            const response = await fetch(url, {method: 'GET', credentials: 'same-origin'});
            let payload;
            try {
                payload = await response.json();
            } catch (_error) {
                throw new Error('查询服务响应异常，请稍后重试。');
            }
            if (!response.ok || !payload.success) {
                throw new Error(payload.message || '课程查询失败，请稍后重试。');
            }
            const candidates = payload.data && Array.isArray(payload.data.candidates) ? payload.data.candidates : [];
            candidates.forEach(renderCandidate);
            showStatus(candidates.length ? '找到 ' + candidates.length + ' 门课程，请核对课程信息。' : '未找到课程，请核对日期、教室或教师。', candidates.length ? 'secondary' : 'warning');
        } catch (error) {
            showStatus(error instanceof TypeError ? '网络连接失败，请稍后重试。' : error.message, 'danger');
        } finally {
            button.disabled = false;
            results.setAttribute('aria-busy', 'false');
        }
    });
}());
