(function () {
    'use strict';

    function setFeedback(element, message, tone) {
        if (!element) {
            return;
        }
        element.className = 'activity-feedback' + (tone ? ` activity-feedback--${tone}` : '');
        element.textContent = message;
        element.hidden = !message;
    }

    function activateActivityTab(center, name, updateUrl) {
        center.dataset.activeTab = name;
        center.querySelectorAll('[data-activity-tab]').forEach((button) => {
            button.setAttribute('aria-selected', String(button.dataset.activityTab === name));
        });
        center.querySelectorAll('[data-activity-panel]').forEach((panel) => {
            panel.hidden = panel.dataset.activityPanel !== name;
        });

        if (updateUrl) {
            const url = new URL(window.location.href);
            url.searchParams.set('tab', name);
            window.history.replaceState({}, '', url);
        }
    }

    function initActivityTabs() {
        const center = document.querySelector('.activity-center');
        if (!center) {
            return;
        }

        const initialTab = center.dataset.activeTab === 'records' ? 'records' : 'registration';
        activateActivityTab(center, initialTab, false);
        center.querySelectorAll('[data-activity-tab]').forEach((button) => {
            button.addEventListener('click', () => activateActivityTab(center, button.dataset.activityTab, true));
        });
    }

    function showCourseDetail(course) {
        const courseDetail = document.getElementById('courseDetailSection');
        const initialTip = document.getElementById('initialTip');
        if (!courseDetail) {
            return;
        }

        $('#courseCode').val(course.course_code || '');
        $('#selectionCode').val(course.selection_code || '');
        $('#dispCourseName').text(course.course_name || '未知');
        $('#dispTeacher').text(`${course.teacher_name || '未知'}${course.teacher_college ? ` (${course.teacher_college})` : ''}`);
        $('#dispTime').text(course.class_time || '未知');
        $('#dispLocation').text(course.class_location || '未知');
        $('#dispCode').text(`课程号: ${course.course_code || '-'} | 选课号: ${course.selection_code || '-'}`);
        courseDetail.hidden = false;
        if (initialTip) {
            initialTip.hidden = true;
        }
        loadHistory(false);
    }

    function renderRecentHistory(items) {
        const container = document.getElementById('historyList');
        if (!container) {
            return;
        }
        container.replaceChildren();
        if (!items.length) {
            const empty = document.createElement('div');
            empty.className = 'activity-empty';
            empty.textContent = '暂无登记记录';
            container.appendChild(empty);
            return;
        }

        items.forEach((item) => {
            const entry = document.createElement('div');
            entry.className = 'list-group-item';
            const meta = document.createElement('div');
            meta.className = 'd-flex justify-content-between gap-2';
            const time = document.createElement('small');
            time.className = 'text-muted';
            time.textContent = item.created_at || '-';
            const status = document.createElement('span');
            status.className = item.is_used ? 'badge bg-success' : 'badge bg-secondary';
            status.textContent = item.is_used ? '已填表' : '未填表';
            meta.append(time, status);
            const description = document.createElement('p');
            description.className = 'mb-0 text-truncate';
            description.title = item.listening_info || '';
            description.textContent = item.listening_info || '无备注';
            entry.append(meta, description);
            container.appendChild(entry);
        });
    }

    function renderAllHistory(items) {
        const body = document.getElementById('allHistoryTableBody');
        if (!body) {
            return;
        }
        body.replaceChildren();
        if (!items.length) {
            const row = document.createElement('tr');
            const cell = document.createElement('td');
            cell.colSpan = 3;
            cell.className = 'text-center text-muted';
            cell.textContent = '暂无记录';
            row.appendChild(cell);
            body.appendChild(row);
        } else {
            items.forEach((item) => {
                const row = document.createElement('tr');
                [item.created_at || '-', item.listening_info || '-', item.is_used ? '已填表' : '未填表'].forEach((value, index) => {
                    const cell = document.createElement('td');
                    cell.textContent = value;
                    if (index === 2) {
                        cell.className = item.is_used ? 'text-success' : 'text-muted';
                    }
                    row.appendChild(cell);
                });
                body.appendChild(row);
            });
        }
        const modalElement = document.getElementById('allHistoryModal');
        if (modalElement && window.bootstrap) {
            bootstrap.Modal.getOrCreateInstance(modalElement).show();
        }
    }

    function loadHistory(showAll, announce) {
        const courseCode = $('#courseCode').val();
        const selectionCode = $('#selectionCode').val();
        if (!courseCode || !selectionCode) {
            return;
        }
        const feedback = document.getElementById('registrationFeedback');
        if (announce !== false) {
            setFeedback(feedback, '正在加载登记历史', 'info');
        }
        $.get('/user/api/course_registration_history', {
            course_code: courseCode,
            selection_code: selectionCode,
            limit: showAll ? 100 : 5
        }).done((response) => {
            if (!response.success) {
                setFeedback(feedback, response.message || '加载历史记录失败，请稍后重试', 'error');
                return;
            }
            if (showAll) {
                renderAllHistory(response.data || []);
            } else {
                renderRecentHistory(response.data || []);
            }
            if (announce !== false) {
                setFeedback(feedback, '', '');
            }
        }).fail((xhr) => {
            const message = xhr.responseJSON && xhr.responseJSON.message ? xhr.responseJSON.message : '加载历史记录失败，请稍后重试';
            setFeedback(feedback, message, 'error');
            if (!showAll) {
                renderRecentHistory([]);
            }
        });
    }

    function submitReservation() {
        const feedback = document.getElementById('registrationFeedback');
        const payload = {
            course_code: $('#courseCode').val(),
            selection_code: $('#selectionCode').val(),
            listening_info: $('#listeningInfo').val()
        };
        if (!payload.listening_info || !payload.listening_info.trim()) {
            setFeedback(feedback, '请填写听课计划说明', 'error');
            return;
        }

        const button = document.getElementById('submitReservationButton');
        if (button) {
            button.disabled = true;
        }
        setFeedback(feedback, '正在提交登记', 'info');
        $.ajax({
            url: '/user/api/create_reservation',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(payload)
        }).done((response) => {
            if (!response.success) {
                setFeedback(feedback, `登记失败：${response.message || '请稍后重试'}`, 'error');
                return;
            }
            $('#listeningInfo').val('');
            setFeedback(feedback, '登记成功', 'success');
            loadHistory(false, false);
        }).fail((xhr) => {
            const message = xhr.responseJSON && xhr.responseJSON.message ? xhr.responseJSON.message : '系统错误，请重试';
            setFeedback(feedback, `登记失败：${message}`, 'error');
        }).always(() => {
            if (button) {
                button.disabled = false;
            }
        });
    }

    function getTimeSuggestion() {
        const feedback = document.getElementById('registrationFeedback');
        setFeedback(feedback, '正在获取时间建议', 'info');
        $.get('/user/api/time_suggestion').done((response) => {
            if (!response.success) {
                setFeedback(feedback, response.message || '获取建议失败，请稍后重试', 'error');
                return;
            }
            $('#suggestionText').text(response.suggestion || '暂无建议');
            document.getElementById('timeSuggestion').hidden = false;
            setFeedback(feedback, '', '');
        }).fail((xhr) => {
            const message = xhr.responseJSON && xhr.responseJSON.message ? xhr.responseJSON.message : '获取建议失败，请稍后重试';
            setFeedback(feedback, message, 'error');
        });
    }

    function useSuggestion() {
        $('#listeningInfo').val($('#suggestionText').text());
        const suggestion = document.getElementById('timeSuggestion');
        if (suggestion) {
            suggestion.hidden = true;
        }
    }

    function initRegistrationPanel() {
        const courseSelect = $('#courseSelect');
        if (!courseSelect.length) {
            return;
        }

        if ($.fn.select2) {
            courseSelect.select2({
                theme: 'bootstrap-5',
                language: {
                    inputTooShort: () => '请输入至少 1 个字符进行搜索',
                    searching: () => '搜索中...',
                    noResults: () => '未找到匹配的课程'
                },
                ajax: {
                    url: '/user/api/available_courses',
                    dataType: 'json',
                    delay: 250,
                    data: (params) => ({q: params.term, page: params.page || 1}),
                    processResults: (data, params) => ({
                        results: data.results || [],
                        pagination: {more: Boolean(data.pagination && data.pagination.more)}
                    }),
                    cache: true
                },
                placeholder: '请输入课程名称、教师或课程号',
                minimumInputLength: 1
            });
            courseSelect.on('select2:select', (event) => showCourseDetail(event.params.data));
            courseSelect.on('select2:clear', () => {
                const detail = document.getElementById('courseDetailSection');
                const tip = document.getElementById('initialTip');
                if (detail) detail.hidden = true;
                if (tip) tip.hidden = false;
            });
        }

        $('#submitReservationButton').on('click', submitReservation);
        $('#viewAllHistoryButton').on('click', () => loadHistory(true));
        $('#timeSuggestionButton').on('click', getTimeSuggestion);
        $('#useSuggestionButton').on('click', useSuggestion);
    }

    let deleteFormId = null;

    window.deleteForm = function (formId) {
        deleteFormId = formId;
        const modal = document.getElementById('deleteModal');
        if (modal && window.bootstrap) {
            bootstrap.Modal.getOrCreateInstance(modal).show();
        }
    };

    window.editReservation = function (reservationId, originalInfo) {
        const nextInfo = window.prompt('请修改听课计划说明：', originalInfo || '');
        if (nextInfo === null) {
            return;
        }
        const payload = {listening_info: nextInfo.trim()};
        if (!payload.listening_info) {
            window.alert('听课计划说明不能为空');
            return;
        }
        fetch(`/user/api/my_reservations/${reservationId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        }).then((response) => response.json()).then((result) => {
            if (!result.success) {
                throw new Error(result.message || '修改失败');
            }
            window.location.reload();
        }).catch((error) => window.alert(`修改失败：${error.message}`));
    };

    window.deleteReservation = function (reservationId) {
        if (!window.confirm('确定删除该听课登记记录吗？此操作不可撤销。')) {
            return;
        }
        fetch(`/user/api/my_reservations/${reservationId}`, {method: 'DELETE'})
            .then((response) => response.json())
            .then((result) => {
                if (!result.success) {
                    throw new Error(result.message || '删除失败');
                }
                window.location.reload();
            })
            .catch((error) => window.alert(`删除失败：${error.message}`));
    };

    window.toggleVersions = function (uniqueId) {
        const row = document.getElementById(`versions-${uniqueId}`);
        if (row) {
            row.hidden = !row.hidden;
        }
    };

    function initRecordsPanel() {
        const confirmButton = document.getElementById('confirmDelete');
        if (confirmButton) {
            confirmButton.addEventListener('click', () => {
                if (!deleteFormId) {
                    return;
                }
                fetch(`/user/delete_form/${deleteFormId}`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'}
                }).then((response) => response.json()).then((result) => {
                    if (!result.success) {
                        throw new Error(result.message || '删除失败');
                    }
                    window.location.reload();
                }).catch((error) => window.alert(`删除失败：${error.message}`));
            });
        }

        const dateFrom = document.getElementById('date_from');
        const dateTo = document.getElementById('date_to');
        if (dateFrom && dateTo && !dateFrom.value && !dateTo.value) {
            const today = new Date();
            const oneMonthAgo = new Date(today.getFullYear(), today.getMonth() - 1, today.getDate());
            dateFrom.value = oneMonthAgo.toISOString().split('T')[0];
            dateTo.value = today.toISOString().split('T')[0];
        }
    }

    window.displayCourseDetails = showCourseDetail;
    window.loadHistory = loadHistory;
    window.submitReservation = submitReservation;
    window.getTimeSuggestion = getTimeSuggestion;
    window.useSuggestion = useSuggestion;

    document.addEventListener('DOMContentLoaded', () => {
        initActivityTabs();
        initRegistrationPanel();
        initRecordsPanel();
    });
}());
