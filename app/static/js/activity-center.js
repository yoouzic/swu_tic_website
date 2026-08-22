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

    function clearCourseSelection() {
        $('#courseCode').val('');
        $('#selectionCode').val('');
        const detail = document.getElementById('courseDetailSection');
        const tip = document.getElementById('initialTip');
        if (detail) {
            detail.hidden = true;
        }
        if (tip) {
            tip.hidden = false;
        }
    }

    function courseResultLabel(course) {
        return course.text || `${course.course_name || '未知课程'} | ${course.teacher_name || '未知教师'} | ${course.class_time || '时间未知'} (${course.course_code || '-'}-${course.selection_code || '-'})`;
    }

    function closeCourseResults() {
        const input = document.getElementById('courseSearchInput');
        const results = document.getElementById('courseResults');
        if (results) {
            results.replaceChildren();
        }
        if (input) {
            input.setAttribute('aria-expanded', 'false');
        }
    }

    function selectCourse(course) {
        const input = document.getElementById('courseSearchInput');
        if (input) {
            input.value = courseResultLabel(course);
        }
        closeCourseResults();
        setFeedback(document.getElementById('courseSearchFeedback'), '', '');
        showCourseDetail(course);
    }

    function renderCourseResults(items) {
        const input = document.getElementById('courseSearchInput');
        const results = document.getElementById('courseResults');
        if (!results) {
            return;
        }
        results.replaceChildren();
        if (!items.length) {
            if (input) {
                input.setAttribute('aria-expanded', 'false');
            }
            setFeedback(document.getElementById('courseSearchFeedback'), '未找到匹配的课程', 'info');
            return;
        }

        if (input) {
            input.setAttribute('aria-expanded', 'true');
        }
        setFeedback(document.getElementById('courseSearchFeedback'), '', '');
        items.forEach((course, index) => {
            const result = document.createElement('button');
            result.type = 'button';
            result.className = 'course-result';
            result.dataset.courseIndex = String(index);
            result.setAttribute('role', 'option');
            result.setAttribute('aria-selected', 'false');
            result.textContent = courseResultLabel(course);
            result.addEventListener('click', () => selectCourse(course));
            result.addEventListener('keydown', (event) => {
                const resultButtons = Array.from(results.querySelectorAll('[data-course-index]'));
                const currentIndex = resultButtons.indexOf(result);
                if (event.key === 'ArrowDown' && resultButtons[currentIndex + 1]) {
                    event.preventDefault();
                    resultButtons[currentIndex + 1].focus();
                } else if (event.key === 'ArrowUp' && resultButtons[currentIndex - 1]) {
                    event.preventDefault();
                    resultButtons[currentIndex - 1].focus();
                } else if (event.key === 'ArrowUp' && currentIndex === 0) {
                    event.preventDefault();
                    input.focus();
                } else if (event.key === 'Escape') {
                    closeCourseResults();
                    input.focus();
                }
            });
            results.appendChild(result);
        });
    }

    let courseSearchTimer = null;
    let courseSearchToken = 0;

    function searchAvailableCourses(query) {
        const feedback = document.getElementById('courseSearchFeedback');
        const input = document.getElementById('courseSearchInput');
        const token = ++courseSearchToken;
        if (!query) {
            closeCourseResults();
            setFeedback(feedback, '请输入至少 1 个字符进行搜索', 'info');
            clearCourseSelection();
            return;
        }

        if (input) {
            input.setAttribute('aria-busy', 'true');
        }
        setFeedback(feedback, '正在加载课程', 'info');
        fetch(`/user/api/available_courses?q=${encodeURIComponent(query)}&page=1`, {
            headers: {'Accept': 'application/json'}
        }).then((response) => response.json().then((data) => ({response, data}))).then(({response, data}) => {
            if (token !== courseSearchToken) {
                return;
            }
            if (!response.ok || !data.success) {
                throw new Error(data.message || '课程加载失败，请稍后重试');
            }
            renderCourseResults(data.results || []);
        }).catch((error) => {
            if (token !== courseSearchToken) {
                return;
            }
            closeCourseResults();
            setFeedback(feedback, error.message || '课程加载失败，请稍后重试', 'error');
        }).finally(() => {
            if (token === courseSearchToken && input) {
                input.removeAttribute('aria-busy');
            }
        });
    }

    function initCourseSearch() {
        const input = document.getElementById('courseSearchInput');
        if (!input) {
            return;
        }
        input.addEventListener('input', () => {
            window.clearTimeout(courseSearchTimer);
            courseSearchTimer = window.setTimeout(() => searchAvailableCourses(input.value.trim()), 250);
        });
        input.addEventListener('keydown', (event) => {
            const firstResult = document.querySelector('#courseResults [data-course-index]');
            if (event.key === 'ArrowDown' && firstResult) {
                event.preventDefault();
                firstResult.focus();
            } else if (event.key === 'Escape') {
                closeCourseResults();
            }
        });
    }

    function initRegistrationPanel() {
        if (!document.getElementById('courseSearchInput')) {
            return;
        }
        initCourseSearch();
        $('#submitReservationButton').on('click', submitReservation);
        $('#viewAllHistoryButton').on('click', () => loadHistory(true));
        $('#timeSuggestionButton').on('click', getTimeSuggestion);
        $('#useSuggestionButton').on('click', useSuggestion);
    }

    let deleteFormId = null;
    let editingReservationId = null;
    let deletingReservationId = null;

    function showBootstrapModal(id) {
        const modal = document.getElementById(id);
        if (modal && window.bootstrap) {
            bootstrap.Modal.getOrCreateInstance(modal).show();
        }
        return modal;
    }

    function hideBootstrapModal(id) {
        const modal = document.getElementById(id);
        if (modal && window.bootstrap) {
            bootstrap.Modal.getOrCreateInstance(modal).hide();
        }
    }

    function parseApiResponse(response) {
        return response.json().then((result) => {
            if (!response.ok || !result.success) {
                throw new Error(result.message || '操作失败');
            }
            return result;
        });
    }

    function setRecordsFeedback(message, tone) {
        setFeedback(document.getElementById('recordsFeedback'), message, tone);
    }

    function findReservationNode(selector, reservationId) {
        return Array.from(document.querySelectorAll(selector)).find((node) => node.dataset.reservationId === String(reservationId) || node.dataset.reservationDescription === String(reservationId));
    }

    function removeReservationRow(reservationId) {
        const row = Array.from(document.querySelectorAll('[data-reservation-row]')).find((node) => node.dataset.reservationRow === String(reservationId));
        if (!row) {
            return;
        }
        row.remove();
        const body = document.getElementById('reservationsTableBody');
        if (body && !body.querySelector('[data-reservation-row]')) {
            const tableWrapper = body.closest('.table-responsive');
            if (tableWrapper) {
                tableWrapper.hidden = true;
            }
            let emptyState = document.getElementById('reservationsEmptyState');
            if (!emptyState && tableWrapper && tableWrapper.parentElement) {
                emptyState = document.createElement('div');
                emptyState.id = 'reservationsEmptyState';
                emptyState.className = 'activity-empty';
                emptyState.textContent = '暂无听课预约。可以先切换到“听课登记”选择课程。';
                tableWrapper.parentElement.appendChild(emptyState);
            }
            if (emptyState) {
                emptyState.hidden = false;
            }
        }
    }

    function removeFormRow(formId) {
        const row = Array.from(document.querySelectorAll('[data-form-row]')).find((node) => node.dataset.formRow === String(formId));
        if (!row) {
            return;
        }
        const versionKey = row.dataset.formKey;
        const body = row.closest('tbody');
        row.remove();
        if (versionKey) {
            const versionRow = Array.from(document.querySelectorAll('[data-version-row]')).find((node) => node.dataset.versionRow === versionKey);
            if (versionRow) {
                versionRow.remove();
            }
        }
        if (body && !body.querySelector('[data-form-row]')) {
            const tableWrapper = body.closest('.table-responsive');
            if (tableWrapper) {
                tableWrapper.hidden = true;
            }
            const panelBody = tableWrapper && tableWrapper.closest('.activity-panel__body');
            let emptyState = document.getElementById('formsEmptyState');
            if (!emptyState && panelBody) {
                emptyState = document.createElement('div');
                emptyState.id = 'formsEmptyState';
                emptyState.className = 'activity-empty';
                emptyState.textContent = '暂无听课表单。填写并提交第一份听课表单后，记录会显示在这里。';
                panelBody.appendChild(emptyState);
            }
            if (emptyState) {
                emptyState.hidden = false;
            }
        }
    }

    function openEditReservation(reservationId, originalInfo) {
        editingReservationId = String(reservationId);
        const idInput = document.getElementById('editReservationId');
        const infoInput = document.getElementById('editReservationInfo');
        if (idInput) {
            idInput.value = editingReservationId;
        }
        if (infoInput) {
            infoInput.value = originalInfo || '';
        }
        setFeedback(document.getElementById('editReservationFeedback'), '', '');
        const modal = showBootstrapModal('editReservationModal');
        if (modal) {
            modal.addEventListener('shown.bs.modal', () => infoInput && infoInput.focus(), {once: true});
        }
    }

    function saveReservationEdit(event) {
        event.preventDefault();
        const idInput = document.getElementById('editReservationId');
        const infoInput = document.getElementById('editReservationInfo');
        const feedback = document.getElementById('editReservationFeedback');
        const saveButton = document.getElementById('saveReservationEdit');
        const reservationId = editingReservationId || (idInput && idInput.value);
        const listeningInfo = infoInput ? infoInput.value.trim() : '';
        if (!reservationId || !listeningInfo) {
            setFeedback(feedback, '听课计划说明不能为空', 'error');
            if (infoInput) {
                infoInput.focus();
            }
            return;
        }
        if (saveButton) {
            saveButton.disabled = true;
        }
        setFeedback(feedback, '正在保存修改', 'info');
        fetch(`/user/api/my_reservations/${reservationId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({listening_info: listeningInfo})
        }).then(parseApiResponse).then(() => {
            const description = findReservationNode('[data-reservation-description]', reservationId);
            if (description) {
                description.textContent = listeningInfo;
            }
            const editButton = findReservationNode('[data-reservation-id]', reservationId);
            if (editButton) {
                editButton.dataset.listeningInfo = listeningInfo;
            }
            setFeedback(feedback, '听课预约已更新', 'success');
            setRecordsFeedback('听课预约已更新', 'success');
        }).catch((error) => {
            setFeedback(feedback, error.message || '修改失败，请稍后重试', 'error');
        }).finally(() => {
            if (saveButton) {
                saveButton.disabled = false;
            }
        });
    }

    function openDeleteReservation(reservationId) {
        deletingReservationId = String(reservationId);
        const idInput = document.getElementById('deleteReservationId');
        if (idInput) {
            idInput.value = deletingReservationId;
        }
        setFeedback(document.getElementById('deleteReservationFeedback'), '', '');
        showBootstrapModal('deleteReservationModal');
    }

    function confirmDeleteReservation() {
        const idInput = document.getElementById('deleteReservationId');
        const feedback = document.getElementById('deleteReservationFeedback');
        const button = document.getElementById('confirmDeleteReservation');
        const reservationId = deletingReservationId || (idInput && idInput.value);
        if (!reservationId) {
            setFeedback(feedback, '未找到要删除的预约记录', 'error');
            return;
        }
        if (button) {
            button.disabled = true;
        }
        setFeedback(feedback, '正在删除预约', 'info');
        fetch(`/user/api/my_reservations/${reservationId}`, {method: 'DELETE'})
            .then(parseApiResponse)
            .then(() => {
                removeReservationRow(reservationId);
                setRecordsFeedback('听课预约已删除', 'success');
                hideBootstrapModal('deleteReservationModal');
            })
            .catch((error) => {
                setFeedback(feedback, error.message || '删除失败，请稍后重试', 'error');
            })
            .finally(() => {
                if (button) {
                    button.disabled = false;
                }
            });
    }

    window.deleteForm = function (formId) {
        deleteFormId = formId;
        setFeedback(document.getElementById('deleteFormFeedback'), '', '');
        showBootstrapModal('deleteModal');
    };

    window.editReservation = openEditReservation;
    window.deleteReservation = openDeleteReservation;

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
                const feedback = document.getElementById('deleteFormFeedback');
                if (deleteFormId === null || deleteFormId === undefined) {
                    setFeedback(feedback, '未找到要删除的表单', 'error');
                    return;
                }
                confirmButton.disabled = true;
                setFeedback(feedback, '正在删除表单', 'info');
                fetch(`/user/delete_form/${deleteFormId}`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'}
                }).then(parseApiResponse).then(() => {
                    removeFormRow(deleteFormId);
                    setRecordsFeedback('听课表单已删除', 'success');
                    deleteFormId = null;
                    hideBootstrapModal('deleteModal');
                }).catch((error) => {
                    setFeedback(feedback, error.message || '删除失败，请稍后重试', 'error');
                }).finally(() => {
                    confirmButton.disabled = false;
                });
            });
        }

        const editForm = document.getElementById('editReservationForm');
        if (editForm) {
            editForm.addEventListener('submit', saveReservationEdit);
        }
        const reservationDeleteButton = document.getElementById('confirmDeleteReservation');
        if (reservationDeleteButton) {
            reservationDeleteButton.addEventListener('click', confirmDeleteReservation);
        }

        const localDateString = (date) => {
            const year = date.getFullYear();
            const month = String(date.getMonth() + 1).padStart(2, '0');
            const day = String(date.getDate()).padStart(2, '0');
            return `${year}-${month}-${day}`;
        };
        const dateFrom = document.getElementById('date_from');
        const dateTo = document.getElementById('date_to');
        if (dateFrom && dateTo && !dateFrom.value && !dateTo.value) {
            const today = new Date();
            const oneMonthAgo = new Date(today.getFullYear(), today.getMonth() - 1, today.getDate());
            dateFrom.value = localDateString(oneMonthAgo);
            dateTo.value = localDateString(today);
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
