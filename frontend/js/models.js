const API_BASE = '/api';

let selectedFile = null;
const pollingIntervals = {}; // { modelId: intervalId }
let allModels = [];
let allTypes = [];
let statusFilter = 'all';

// ── Initialise ────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => { loadModelTypes(); loadModels(); });

// ── File Drag & Drop ──────────────────────────────────────────────────────────
function handleDragOver(e) {
    e.preventDefault();
    document.getElementById('drop-zone').classList.add('drag-over');
}
function handleDragLeave(e) {
    document.getElementById('drop-zone').classList.remove('drag-over');
}
function handleDrop(e) {
    e.preventDefault();
    document.getElementById('drop-zone').classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file) setFile(file);
}
function handleFileSelect(e) {
    const file = e.target.files[0];
    if (file) setFile(file);
}
function setFile(file) {
    selectedFile = file;
    document.getElementById('dz-file-name').textContent = `📁 ${file.name}  (${(file.size / 1024).toFixed(1)} KB)`;
    document.getElementById('drop-zone').style.borderColor = 'var(--accent-green)';
}

// ── Type Dropdown Change Handler ──────────────────────────────────────────────
function onModelTypeChange() {
    const sel = document.getElementById('model-type');
    const selectedKey = sel.value;
    const typeObj = allTypes.find(t => t.name === selectedKey);
    if (typeObj) {
        if (typeObj.default_threshold !== undefined) {
            document.getElementById('model-threshold').value = typeObj.default_threshold;
            document.getElementById('threshold-val-display').innerText = Math.round(typeObj.default_threshold * 100) + '%';
        }
        if (typeObj.default_cooldown !== undefined) {
            document.getElementById('model-cooldown').value = typeObj.default_cooldown;
        }
        if (typeObj.default_tracking !== undefined) {
            document.getElementById('model-tracking').checked = !!typeObj.default_tracking;
        }
    }
}

// ── Upload ────────────────────────────────────────────────────────────────────
async function uploadModel() {
    const name = document.getElementById('model-name').value.trim();
    const type = document.getElementById('model-type').value;
    const threshold = parseFloat(document.getElementById('model-threshold').value) || 0.50;
    const cooldown = parseInt(document.getElementById('model-cooldown').value) || 10;
    const tracking = document.getElementById('model-tracking').checked;

    if (!name) { UI.toast('Please enter a model name.', 'error'); return showBanner('failed', '⚠️ Please enter a model name.'); }
    if (!type) { UI.toast('Please select a model type.', 'error'); return showBanner('failed', '⚠️ Please select a model type.'); }
    if (!selectedFile) { UI.toast('Please select a .pt file.', 'error'); return showBanner('failed', '⚠️ Please select a .pt file.'); }
    if (!selectedFile.name.endsWith('.pt')) {
        UI.toast('Only .pt files are accepted.', 'error');
        return showBanner('failed', '⚠️ Only .pt files are accepted.');
    }

    // Show progress
    showProgress(true, 'Uploading…');
    animateProgress(0, 85, 800);
    document.getElementById('upload-btn').disabled = true;
    showBanner('validating', 'Uploading model file…');

    const fd = new FormData();
    fd.append('name', name);
    fd.append('type', type);
    fd.append('file', selectedFile);
    fd.append('confidence_threshold', threshold);
    fd.append('cooldown_seconds', cooldown);
    fd.append('tracking_enabled', tracking);

    const typeObj = allTypes.find(t => t.name === type);
    if (typeObj && typeObj.icon) {
        fd.append('icon', typeObj.icon);
    }

    try {
        const res = await fetch(`${API_BASE}/models/upload`, { method: 'POST', body: fd });

        if (!res.ok) {
            let errorText = 'Unknown error';
            try {
                const err = await res.json();
                errorText = err.detail || errorText;
            } catch (e) {
                // If it's not JSON (like Nginx 413 Payload Too Large HTML)
                errorText = `HTTP Error ${res.status}: ${res.statusText}`;
            }
            animateProgress(85, 100, 200);
            showBanner('failed', `❌ Upload failed: ${errorText}`);
            showProgress(true, 'Failed');
            document.getElementById('upload-btn').disabled = false;
            return;
        }

        const model = await res.json();
        animateProgress(85, 100, 300);
        showProgress(true, 'Upload complete — validating model…');
        showBanner('validating', 'Model uploaded. Running validation…');

        // Refresh list to show new pending entry
        await loadModels();

        // Start polling this model's status
        startPolling(model.id);

        // Reset form
        document.getElementById('model-name').value = '';
        document.getElementById('dz-file-name').textContent = '';
        document.getElementById('drop-zone').style.borderColor = '';
        selectedFile = null;

    } catch (e) {
        showBanner('failed', `❌ Network error: ${e.message}`);
    } finally {
        document.getElementById('upload-btn').disabled = false;
    }
}

// ── Polling ───────────────────────────────────────────────────────────────────
function startPolling(modelId) {
    if (pollingIntervals[modelId]) clearInterval(pollingIntervals[modelId]);

    pollingIntervals[modelId] = setInterval(async () => {
        try {
            const res = await fetch(`${API_BASE}/models/${modelId}/status`);
            const data = await res.json();

            if (data.status === 'active') {
                clearInterval(pollingIntervals[modelId]);
                delete pollingIntervals[modelId];
                showBanner('success', '✅ Validation passed! Model is now Active.');
                UI.toast('Model validated and activated', 'success');
                showProgress(false);
                loadModels();

            } else if (data.status === 'failed') {
                clearInterval(pollingIntervals[modelId]);
                delete pollingIntervals[modelId];
                showBanner('failed', '❌ Validation failed. The model file is invalid or incompatible.');
                UI.toast('Model validation failed', 'error');
                showProgress(false);
                loadModels();

            } else {
                // Still pending / validating — update badge in table live
                updateModelBadge(modelId, data.status);
            }
        } catch (e) {
            // silently ignore transient network errors
        }
    }, 3000);
}

// ── Load Model List ───────────────────────────────────────────────────────────
async function loadModels() {
    const container = document.getElementById('model-list-container');
    if (!allModels.length) container.innerHTML = modelListSkeleton();

    try {
        const res = await fetch(`${API_BASE}/models/`);
        const models = await res.json();
        allModels = models;
        renderModelTable();

        // Resume polling for any models still pending / validating
        models.forEach(m => {
            if ((m.status === 'pending' || m.status === 'validating') && !pollingIntervals[m.id]) {
                startPolling(m.id);
            }
        });
    } catch (e) {
        container.innerHTML =
            '<p style="color:var(--text-secondary);padding:20px;">Failed to load models.</p>';
    }
}

function modelListSkeleton() {
    return `
        <table class="model-table">
            <tbody>
                ${Array.from({ length: 3 }).map(() => `
                    <tr>
                        <td><div class="skeleton-line" style="width:140px;"></div></td>
                        <td><div class="skeleton-line" style="width:90px;"></div></td>
                        <td><div class="skeleton-line" style="width:50px;"></div></td>
                        <td><div class="skeleton-line" style="width:80px;"></div></td>
                        <td><div class="skeleton-line" style="width:110px;"></div></td>
                        <td><div class="skeleton-line" style="width:100px;"></div></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>`;
}

function setStatusFilter(status, btn) {
    statusFilter = status;
    document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    renderModelTable();
}

// Renders allModels into #model-list-container, applying search + status filter.
function renderModelTable() {
    const container = document.getElementById('model-list-container');
    const searchEl = document.getElementById('model-search');
    const term = (searchEl ? searchEl.value : '').trim().toLowerCase();

    if (!allModels.length) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="es-icon">🤖</div>
                No models uploaded yet. Upload your first YOLO model above.
            </div>`;
        return;
    }

    const models = allModels.filter(m => {
        if (statusFilter !== 'all' && m.status !== statusFilter) return false;
        if (term && !m.name.toLowerCase().includes(term) && !m.type.toLowerCase().includes(term)) return false;
        return true;
    });

    if (!models.length) {
        container.innerHTML = `<div class="no-results">🔎 No models match your search/filter.</div>`;
        return;
    }

    const rows = models.map(m => {
        const iconClass = m.icon || 'fa-cube';
        const thresholdPct = Math.round((m.confidence_threshold || 0.50) * 100);
        const cooldown = m.cooldown_seconds !== undefined ? m.cooldown_seconds : 10;
        const tracking = m.tracking_enabled !== false;

        return `
        <tr id="model-row-${m.id}">
            <td>
                <div style="display:flex; align-items:center;">
                    <span class="icon-badge"><i class="fa-solid ${escHtml(iconClass)}"></i></span>
                    <strong>${escHtml(m.name)}</strong>
                </div>
            </td>
            <td><span class="type-chip">${escHtml(m.type)}</span></td>
            <td>
                <span style="font-weight:600; color:var(--accent-blue); background:rgba(59,130,246,0.1); padding:2px 8px; border-radius:6px; font-size:0.82rem;">
                    ${thresholdPct}%
                </span>
            </td>
            <td>
                <span style="font-size:0.82rem; color:var(--text-secondary);">
                    ⏱️ ${cooldown}s
                </span>
            </td>
            <td>
                <span class="badge ${tracking ? 'badge-active' : 'badge-inactive'}" style="font-size:0.75rem;">
                    ${tracking ? '🎯 On' : 'Off'}
                </span>
            </td>
            <td>${badgeHtml(m.id, m.status)}</td>
            <td>
                <div class="action-btns">
                    <button class="btn" onclick="openEditModal(${m.id})" title="Configure Threshold, Tracking & Cooldown">⚙️ Edit</button>
                    ${m.status === 'active'
            ? `<button class="btn" onclick="setModelStatus(${m.id},'deactivate')">⏸</button>`
            : `<button class="btn btn-primary" onclick="setModelStatus(${m.id},'activate')">▶</button>`
        }
                    <button class="btn btn-danger" onclick="deleteModel(${m.id})">🗑️</button>
                </div>
            </td>
        </tr>
    `;
    }).join('');

    container.innerHTML = `
        <table class="model-table">
            <thead>
                <tr>
                    <th>Model Name</th>
                    <th>Type</th>
                    <th>Threshold</th>
                    <th>Cooldown</th>
                    <th>Tracking</th>
                    <th>Status</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>`;
}

// ── Edit Model Settings Modal ────────────────────────────────────────────────
function openEditModal(modelId) {
    const model = allModels.find(m => m.id === modelId);
    if (!model) return;

    document.getElementById('edit-model-id').value = model.id;
    document.getElementById('edit-model-name').value = model.name;
    
    const thresh = model.confidence_threshold !== undefined ? model.confidence_threshold : 0.50;
    document.getElementById('edit-model-threshold').value = thresh;
    document.getElementById('edit-threshold-val').innerText = Math.round(thresh * 100) + '%';
    
    document.getElementById('edit-model-cooldown').value = model.cooldown_seconds !== undefined ? model.cooldown_seconds : 10;
    document.getElementById('edit-model-tracking').checked = model.tracking_enabled !== false;
    
    const iconEl = document.getElementById('edit-model-icon');
    if (iconEl) {
        iconEl.innerHTML = `<i class="fa-solid ${escHtml(model.icon || 'fa-cube')}"></i>`;
    }

    document.getElementById('edit-model-modal').classList.add('active');
}

function closeEditModal() {
    document.getElementById('edit-model-modal').classList.remove('active');
}

async function saveModelEdit() {
    const modelId = parseInt(document.getElementById('edit-model-id').value);
    const name = document.getElementById('edit-model-name').value.trim();
    const threshold = parseFloat(document.getElementById('edit-model-threshold').value);
    const cooldown = parseInt(document.getElementById('edit-model-cooldown').value);
    const tracking = document.getElementById('edit-model-tracking').checked;

    if (!name) {
        UI.toast('Please provide a name', 'error');
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/models/${modelId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                confidence_threshold: threshold,
                cooldown_seconds: cooldown,
                tracking_enabled: tracking
            })
        });

        if (!res.ok) {
            const err = await res.json();
            UI.toast(err.detail || 'Failed to update model', 'error');
            return;
        }

        UI.toast('Model settings saved successfully', 'success');
        closeEditModal();
        await loadModels();
    } catch (e) {
        console.error(e);
        UI.toast('Network error updating model', 'error');
    }
}

// ── Live badge update (during polling) ───────────────────────────────────────
function updateModelBadge(modelId, status) {
    const cell = document.querySelector(`#model-row-${modelId} td:nth-child(4)`);
    if (cell) cell.innerHTML = badgeHtml(modelId, status);
}

// ── Status change ─────────────────────────────────────────────────────────────
async function setModelStatus(modelId, action) {
    try {
        await fetch(`${API_BASE}/models/${modelId}/${action}`, { method: 'POST' });
        UI.toast(action === 'activate' ? 'Model activated' : 'Model deactivated', 'success');
        loadModels();
    } catch (e) {
        console.error(e);
        UI.toast('Failed to update model status', 'error');
    }
}

// ── Delete ────────────────────────────────────────────────────────────────────
async function deleteModel(modelId) {
    const ok = await UI.confirm('This also removes the .pt file from disk.', { title: 'Delete this model?' });
    if (!ok) return;
    try {
        await fetch(`${API_BASE}/models/${modelId}`, { method: 'DELETE' });
        // Stop any ongoing poll for this model
        if (pollingIntervals[modelId]) {
            clearInterval(pollingIntervals[modelId]);
            delete pollingIntervals[modelId];
        }
        UI.toast('Model deleted', 'success');
        loadModels();
    } catch (e) {
        console.error(e);
        UI.toast('Failed to delete model', 'error');
    }
}

// ── Model Type Management ─────────────────────────────────────────────────
function openTypesModal() {
    document.getElementById('types-modal').classList.add('active');
}

function closeTypesModal() {
    document.getElementById('types-modal').classList.remove('active');
}

function populateTypeDropdown(types) {
    const sel = document.getElementById('model-type');
    sel.innerHTML = types.map(t =>
        `<option value="${escHtml(t.name)}">${t.icon ? '● ' : ''}${escHtml(t.display_name)}</option>`
    ).join('');
    onModelTypeChange();
}

async function loadModelTypes() {
    try {
        const res = await fetch(`${API_BASE}/models/types`);
        allTypes = await res.json();
        populateTypeDropdown(allTypes);
        renderModelTypes();
    } catch (e) {
        console.error('Failed to load model types', e);
    }
}

function renderModelTypes() {
    const container = document.getElementById('model-types-container');
    if (!container) return;
    if (!allTypes.length) {
        container.innerHTML = '<p style="color:var(--text-secondary); text-align:center; padding:16px;">No types defined. Add one above.</p>';
        return;
    }
    const rows = allTypes.map(t => `
        <tr>
            <td><code style="background:rgba(59,130,246,.1); color:#93c5fd; padding:2px 8px; border-radius:5px;">${escHtml(t.name)}</code></td>
            <td style="color:var(--text-primary);">${escHtml(t.display_name)}</td>
            <td>
                <button class="btn btn-danger" style="padding:4px 10px; font-size:0.78rem;"
                    onclick="deleteModelType(${t.id}, '${escHtml(t.display_name)}')">
                    🗑️ Delete
                </button>
            </td>
        </tr>
    `).join('');

    container.innerHTML = `
        <table class="model-table">
            <thead>
                <tr>
                    <th>Key</th>
                    <th>Display Name</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>`;
}

async function addModelType() {
    const nameEl = document.getElementById('new-type-name');
    const dispEl = document.getElementById('new-type-display');
    const name = nameEl.value.trim();
    const display_name = dispEl.value.trim();

    if (!name) { UI.toast('Enter an internal key.', 'error'); return; }
    if (/\s/.test(name)) { UI.toast('Internal key cannot contain spaces.', 'error'); return; }
    if (!display_name) { UI.toast('Enter a display name.', 'error'); return; }

    try {
        const res = await fetch(`${API_BASE}/models/types`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, display_name })
        });
        if (!res.ok) {
            const err = await res.json();
            UI.toast(err.detail || 'Failed to add type', 'error');
            return;
        }
        nameEl.value = '';
        dispEl.value = '';
        UI.toast('Type added!', 'success');
        await loadModelTypes();
    } catch (e) {
        UI.toast('Network error', 'error');
    }
}

async function deleteModelType(typeId, name) {
    const ok = await UI.confirm(`Remove type "${name}"? Existing models using this type will keep their data.`, { title: 'Delete Type?' });
    if (!ok) return;
    try {
        const res = await fetch(`${API_BASE}/models/types/${typeId}`, { method: 'DELETE' });
        if (res.status === 204 || res.ok) {
            UI.toast('Type deleted', 'success');
            await loadModelTypes();
        } else {
            UI.toast('Failed to delete type', 'error');
        }
    } catch (e) {
        UI.toast('Network error', 'error');
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function badgeHtml(id, status) {
    const map = {
        pending: { cls: 'badge-pending', icon: '⏳', label: 'Pending' },
        validating: { cls: 'badge-validating', icon: '⚙️', label: 'Validating' },
        active: { cls: 'badge-active', icon: '', label: 'Active', dot: true },
        failed: { cls: 'badge-failed', icon: '✖', label: 'Failed' },
        inactive: { cls: 'badge-inactive', icon: '⏸', label: 'Inactive' },
    };
    const s = map[status] || map['pending'];
    const spinner = (status === 'validating') ? `<span class="spinner" style="width:11px;height:11px;border-width:2px;"></span>` : '';
    const dot = s.dot ? `<span class="badge-dot"></span>` : '';
    return `<span class="badge ${s.cls}" id="badge-${id}">${spinner}${dot}${s.icon} ${s.label}</span>`;
}

function formatDate(iso) {
    if (!iso) return '—';
    try {
        const d = new Date(iso);
        return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch { return iso; }
}

function escHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function showBanner(type, msg) {
    const b = document.getElementById('validation-banner');
    b.className = `validation-banner ${type}`;
    b.style.display = 'flex';
    const spinner = document.getElementById('v-spinner');
    spinner.style.display = type === 'validating' ? 'block' : 'none';
    document.getElementById('v-message').textContent = msg;
}

function showProgress(visible, label = '') {
    const wrap = document.getElementById('progress-wrap');
    wrap.style.display = visible ? 'block' : 'none';
    if (label) document.getElementById('progress-label').textContent = label;
}

function animateProgress(from, to, durationMs) {
    const fill = document.getElementById('progress-fill');
    const steps = 30;
    const delta = (to - from) / steps;
    const delay = durationMs / steps;
    let current = from;
    const timer = setInterval(() => {
        current += delta;
        fill.style.width = Math.min(current, 100) + '%';
        if (current >= to) clearInterval(timer);
    }, delay);
}
