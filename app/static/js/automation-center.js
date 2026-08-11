(() => {
    const settingsPanel = document.querySelector('[data-settings-panel="automation"]');
    if (!settingsPanel) return;
    const panel = settingsPanel.querySelector('[data-automation-center]');
    if (!panel) return;

    const feedback = panel.querySelector('#automationSettingsFeedback');
    const previewModal = panel.querySelector('#automationPreviewModal');
    const previewBody = panel.querySelector('#automationPreviewBody');
    const previewActivate = panel.querySelector('#automationPreviewActivate');
    const pendingDatasets = new Map();
    const templateColumns = {
        school: ['semester', 'teacher_name', 'teacher_college', 'course_title', 'teaching_class', 'major', 'weeks', 'weekday', 'start_period', 'end_period', 'location'],
        class_mapping: ['listener_number', 'student_id', 'admin_class'],
        personal: ['semester', 'listener_number', 'student_id', 'course_title', 'weeks', 'weekday', 'start_period', 'end_period'],
    };
    const templateNames = {
        school: 'school-schedule-template.csv',
        class_mapping: 'class-mapping-template.csv',
        personal: 'personal-schedule-template.csv',
    };
    let activePreviewKind = null;
    let initialized = false;

    const text = (value, fallback = '—') => {
        if (value === null || value === undefined || value === '') return fallback;
        return String(value);
    };

    const setFeedback = (message, variant = 'info') => {
        if (!feedback) return;
        feedback.textContent = text(message, '操作完成');
        feedback.className = `alert alert-${variant}`;
        feedback.setAttribute('role', 'status');
        feedback.setAttribute('aria-live', 'polite');
    };

    const jsonRequest = async (url, options = {}) => {
        const response = await fetch(url, options);
        let payload = {};
        try {
            payload = await response.json();
        } catch (error) {
            payload = {message: '服务返回了无法读取的响应'};
        }
        if (!response.ok) {
            const message = payload.message || payload.error || `请求失败（${response.status}）`;
            throw new Error(message);
        }
        return payload;
    };

    const setServiceState = (name, value, variant = 'secondary') => {
        const row = panel.querySelector(`[data-service-state="${name}"]`);
        const badge = row?.querySelector('[data-service-value]');
        if (!badge) return;
        badge.textContent = text(value, 'unchecked');
        badge.className = `badge text-bg-${variant}`;
    };

    const renderHealth = (payload) => {
        const services = payload.services || {};
        const deepseek = services.deepseek === 'configured' ? 'configured' : 'missing';
        setServiceState('redis', services.redis || 'unchecked', services.redis === 'ready' ? 'success' : 'secondary');
        setServiceState('worker', services.worker || 'unchecked', services.worker === 'ready' ? 'success' : 'secondary');
        setServiceState('deepseek', deepseek, deepseek === 'configured' ? 'success' : 'secondary');
        const keyNode = panel.querySelector('#automationDeepseekKeyStatus');
        if (keyNode) keyNode.textContent = deepseek;
    };

    const appendCell = (row, value, className = '') => {
        const cell = document.createElement('td');
        cell.textContent = text(value);
        if (className) cell.className = className;
        row.appendChild(cell);
        return cell;
    };

    const renderCoverage = (payload) => {
        const source = payload?.coverage || {};
        ['complete', 'basic', 'missing', 'total'].forEach((key) => {
            const node = panel.querySelector(`[data-coverage="${key}"]`);
            if (node) node.textContent = text(source[key], '0');
        });
    };

    const renderDatasets = (payload) => {
        renderCoverage(payload);
        const body = panel.querySelector('#automationDatasetHistory');
        if (!body) return;
        body.replaceChildren();
        const datasets = Array.isArray(payload) ? payload : (payload.datasets || []);
        if (!datasets.length) {
            const row = document.createElement('tr');
            const cell = appendCell(row, '暂无数据集');
            cell.colSpan = 6;
            body.appendChild(row);
            return;
        }
        datasets.forEach((dataset) => {
            const row = document.createElement('tr');
            appendCell(row, dataset.kind);
            appendCell(row, dataset.semester);
            appendCell(row, dataset.version || dataset.sha256);
            appendCell(row, dataset.status);
            appendCell(row, dataset.row_count ?? dataset.rows);
            appendCell(row, dataset.updated_at || dataset.created_at);
            body.appendChild(row);
        });
    };

    const parameterText = (parameters) => {
        try {
            return JSON.stringify(parameters || {});
        } catch (error) {
            return '{}';
        }
    };

    const fillRuleEditor = (rule) => {
        const values = {
            '#automationRuleKey': text(rule.rule_key || rule.key, ''),
            '#automationRuleHandler': text(rule.handler, ''),
            '#automationRuleSeverity': text(rule.severity, 'review'),
            '#automationRuleParameters': parameterText(rule.parameters),
            '#automationRuleChangeReason': text(rule.change_reason, ''),
        };
        Object.entries(values).forEach(([selector, value]) => {
            const node = panel.querySelector(selector);
            if (node) node.value = value;
        });
        const enabled = panel.querySelector('#automationRuleEnabled');
        if (enabled) enabled.checked = rule.enabled !== false;
    };

    const showRuleModal = () => {
        const modal = window.bootstrap?.Modal?.getOrCreateInstance(panel.querySelector('#automationRuleRevision'));
        modal?.show();
    };

    const renderRules = (payload) => {
        const body = panel.querySelector('#automationRuleList');
        if (!body) return;
        body.replaceChildren();
        const rules = Array.isArray(payload) ? payload : (payload.rules || []);
        if (!rules.length) {
            const row = document.createElement('tr');
            const cell = appendCell(row, '暂无规则');
            cell.colSpan = 6;
            body.appendChild(row);
            return;
        }
        rules.forEach((rule) => {
            const row = document.createElement('tr');
            appendCell(row, rule.rule_key || rule.key);
            appendCell(row, rule.severity);
            appendCell(row, rule.version);
            appendCell(row, rule.enabled === false ? 'disabled' : 'enabled');
            appendCell(row, parameterText(rule.parameters));
            const action = document.createElement('td');
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-sm btn-outline-secondary';
            button.textContent = '编辑';
            button.addEventListener('click', () => {
                fillRuleEditor(rule);
                showRuleModal();
            });
            action.appendChild(button);
            row.appendChild(action);
            body.appendChild(row);
        });
    };

    const loadSettingsData = async () => {
        const results = await Promise.allSettled([
            jsonRequest('/admin/api/automation/health'),
            jsonRequest('/admin/api/automation/datasets'),
            jsonRequest('/admin/api/automation/rules'),
        ]);
        const labels = ['服务状态', '数据集历史', '规则列表'];
        results.forEach((result, index) => {
            if (result.status === 'rejected') {
                setFeedback(`${labels[index]}加载失败：${result.reason.message}`, 'warning');
                return;
            }
            if (index === 0) renderHealth(result.value);
            if (index === 1) renderDatasets(result.value);
            if (index === 2) renderRules(result.value);
        });
    };

    const getPreviewModal = () => window.bootstrap?.Modal?.getOrCreateInstance(previewModal);

    const showPreview = (kind, dataset) => {
        activePreviewKind = kind;
        pendingDatasets.set(kind, dataset.id);
        const summary = dataset.summary || dataset.preview || {};
        if (previewBody) {
            previewBody.replaceChildren();
            const title = document.createElement('p');
            title.textContent = `${text(dataset.kind || kind)} · ${text(dataset.semester)}`;
            const details = document.createElement('p');
            details.textContent = `状态：${text(dataset.status)}；有效行：${text(dataset.row_count ?? summary.valid_rows, '0')}；问题：${text(dataset.error_count ?? summary.error_count, '0')}`;
            previewBody.append(title, details);
        }
        if (previewActivate) previewActivate.disabled = !dataset.id || dataset.status === 'failed';
    };

    const previewDataset = async (button) => {
        const kind = button.dataset.kind;
        const card = panel.querySelector(`[data-dataset-kind="${kind}"]`);
        const file = card?.querySelector('[data-automation-file]')?.files?.[0];
        const semester = panel.querySelector('#automationSemester')?.value.trim();
        if (!file || !semester) {
            setFeedback('请先选择文件并填写学期。', 'warning');
            return;
        }
        const formData = new FormData();
        formData.append('file', file);
        formData.append('semester', semester);
        try {
            setFeedback('正在生成数据集预览……', 'info');
            const payload = await jsonRequest(`/admin/api/automation/datasets/${encodeURIComponent(kind)}/preview`, {method: 'POST', body: formData});
            const dataset = payload.dataset || payload;
            showPreview(kind, dataset);
            const summary = card?.querySelector('[data-preview-summary]');
            if (summary) summary.textContent = `已生成预览：${text(dataset.status)}`;
            const activate = card?.querySelector('[data-automation-activate]');
            if (activate) {
                activate.hidden = !dataset.id || dataset.status === 'failed';
                activate.dataset.datasetId = text(dataset.id, '');
            }
            getPreviewModal()?.show();
            setFeedback('预览已生成，请确认后启用。', 'success');
        } catch (error) {
            setFeedback(error.message, 'danger');
        }
    };

    const activateDataset = async (kind, datasetId) => {
        if (!datasetId) return;
        try {
            setFeedback('正在启用数据集……', 'info');
            await jsonRequest(`/admin/api/automation/datasets/${encodeURIComponent(datasetId)}/activate`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({})});
            const card = panel.querySelector(`[data-dataset-kind="${kind}"]`);
            const summary = card?.querySelector('[data-preview-summary]');
            if (summary) summary.textContent = '已启用';
            const activate = card?.querySelector('[data-automation-activate]');
            if (activate) activate.hidden = true;
            pendingDatasets.delete(kind);
            getPreviewModal()?.hide();
            const datasets = await jsonRequest('/admin/api/automation/datasets');
            renderDatasets(datasets);
            setFeedback('数据集已启用，历史记录和覆盖统计已刷新。', 'success');
        } catch (error) {
            setFeedback(error.message, 'danger');
        }
    };

    const submitRuleRevision = async (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const ruleKey = panel.querySelector('#automationRuleKey')?.value.trim();
        const handler = panel.querySelector('#automationRuleHandler')?.value.trim();
        const enabled = panel.querySelector('#automationRuleEnabled')?.checked === true;
        const severity = panel.querySelector('#automationRuleSeverity')?.value;
        const parametersText = panel.querySelector('#automationRuleParameters')?.value.trim() || '{}';
        const changeReason = panel.querySelector('#automationRuleChangeReason')?.value.trim();
        if (!ruleKey || !handler || !changeReason) {
            setFeedback('规则 key、处理器和变更原因不能为空。', 'warning');
            return;
        }
        let parameters;
        try {
            parameters = JSON.parse(parametersText);
        } catch (error) {
            setFeedback('parameters 必须是有效 JSON。', 'warning');
            return;
        }
        if (!parameters || Array.isArray(parameters) || typeof parameters !== 'object') {
            setFeedback('parameters 必须是 JSON 对象。', 'warning');
            return;
        }
        try {
            await jsonRequest(`/admin/api/automation/rules/${encodeURIComponent(ruleKey)}/revisions`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({handler, enabled, severity, parameters, change_reason: changeReason})});
            form.reset();
            setFeedback('规则 revision 已保存。', 'success');
            const rules = await jsonRequest('/admin/api/automation/rules');
            renderRules(rules);
        } catch (error) {
            setFeedback(error.message, 'danger');
        }
    };

    const downloadTemplate = (event) => {
        const kind = event.currentTarget.dataset.kind;
        const columns = templateColumns[kind];
        const filename = templateNames[kind];
        if (!columns || !filename) return;
        const csv = `\uFEFF${columns.join(',')}\r\n`;
        const blob = new Blob([csv], {type: 'text/csv;charset=utf-8'});
        const objectUrl = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = objectUrl;
        anchor.download = filename;
        anchor.setAttribute('aria-hidden', 'true');
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(objectUrl);
        setFeedback('模板已下载。', 'success');
    };

    const init = () => {
        if (initialized) return;
        initialized = true;
        panel.dataset.automationInitialized = 'true';
        panel.querySelectorAll('[data-automation-preview]').forEach((button) => {
            button.addEventListener('click', () => previewDataset(button));
        });
        panel.querySelectorAll('[data-automation-activate]').forEach((button) => {
            button.addEventListener('click', () => activateDataset(button.dataset.kind, button.dataset.datasetId));
        });
        panel.querySelectorAll('[data-automation-download]').forEach((button) => button.addEventListener('click', downloadTemplate));
        panel.querySelector('#automationPreviewActivate')?.addEventListener('click', () => {
            const datasetId = pendingDatasets.get(activePreviewKind);
            activateDataset(activePreviewKind, datasetId);
        });
        panel.querySelector('#automationRuleRevisionForm')?.addEventListener('submit', submitRuleRevision);
        loadSettingsData().catch((error) => setFeedback(error.message, 'danger'));
    };

    settingsPanel.addEventListener('settings:shown', init);
})();
