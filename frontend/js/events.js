const API_BASE = '/api';

let currentPage = 1;
const pageSize = 30;
let totalEvents = 0;
let currentEvents = [];
let allShops = [];
let allCameras = [];

document.addEventListener('DOMContentLoaded', async () => {
    await loadFilterOptions();
    await loadEvents();
});

// ── Icon & Color Mapping ──────────────────────────────────────────────────────
const CATEGORY_META = {
    human: { icon: 'fa-user', label: 'Human', color: '#60a5fa', bg: 'rgba(59, 130, 246, 0.2)' },
    yolo_human: { icon: 'fa-user', label: 'Human', color: '#60a5fa', bg: 'rgba(59, 130, 246, 0.2)' },
    fire: { icon: 'fa-fire', label: 'Fire Alert', color: '#f87171', bg: 'rgba(239, 68, 68, 0.25)' },
    yolo_fire: { icon: 'fa-fire', label: 'Fire Alert', color: '#f87171', bg: 'rgba(239, 68, 68, 0.25)' },
    helmet: { icon: 'fa-hard-hat', label: 'Helmet/PPE', color: '#fbbf24', bg: 'rgba(245, 158, 11, 0.2)' },
    vehicle: { icon: 'fa-car', label: 'Vehicle', color: '#34d399', bg: 'rgba(16, 185, 129, 0.2)' },
    weapon: { icon: 'fa-crosshairs', label: 'Weapon Alert', color: '#ef4444', bg: 'rgba(239, 68, 68, 0.3)' },
    yolo_weapon: { icon: 'fa-crosshairs', label: 'Weapon Alert', color: '#ef4444', bg: 'rgba(239, 68, 68, 0.3)' },
    motion: { icon: 'fa-circle-dot', label: 'Motion', color: '#fbbf24', bg: 'rgba(245, 158, 11, 0.2)' },
    default: { icon: 'fa-cube', label: 'Object', color: '#93c5fd', bg: 'rgba(59, 130, 246, 0.2)' }
};

function getCategoryMeta(type) {
    if (!type) return CATEGORY_META.default;
    const key = type.toLowerCase();
    for (const [k, v] of Object.entries(CATEGORY_META)) {
        if (key === k || key.includes(k)) return v;
    }
    return CATEGORY_META.default;
}

// ── Load Filter Options (Shops & Cameras) ────────────────────────────────────
async function loadFilterOptions() {
    try {
        const [shopsRes, camsRes] = await Promise.all([
            fetch(`${API_BASE}/shops`),
            fetch(`${API_BASE}/cameras`)
        ]);
        
        allShops = await shopsRes.json();
        allCameras = await camsRes.json();

        const shopSelect = document.getElementById('filter-shop');
        allShops.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.id;
            opt.textContent = s.name;
            shopSelect.appendChild(opt);
        });

        populateCameraDropdown();
    } catch (e) {
        console.error("Failed to load shops/cameras for filtering", e);
    }
}

function onShopFilterChange() {
    populateCameraDropdown();
    loadEvents();
}

function populateCameraDropdown() {
    const shopId = document.getElementById('filter-shop').value;
    const camSelect = document.getElementById('filter-camera');
    camSelect.innerHTML = '<option value="">All Cameras</option>';

    const filteredCams = shopId 
        ? allCameras.filter(c => String(c.shop_id) === String(shopId))
        : allCameras;

    filteredCams.forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.id;
        opt.textContent = c.name;
        camSelect.appendChild(opt);
    });
}

function onDatePresetChange() {
    const preset = document.getElementById('filter-date-preset').value;
    const customWrap = document.getElementById('custom-date-wrap');
    if (preset === 'custom') {
        customWrap.style.display = 'flex';
    } else {
        customWrap.style.display = 'none';
        loadEvents();
    }
}

function resetFilters() {
    document.getElementById('filter-date-preset').value = 'today';
    document.getElementById('custom-date-wrap').style.display = 'none';
    document.getElementById('filter-custom-date').value = '';
    document.getElementById('filter-category').value = 'all';
    document.getElementById('filter-shop').value = '';
    document.getElementById('filter-min-conf').value = '0';
    document.getElementById('conf-val-label').innerText = '0%';
    populateCameraDropdown();
    loadEvents();
}

// ── Fetch & Render Events ─────────────────────────────────────────────────────
async function loadEvents(isAppend = false) {
    if (!isAppend) {
        currentPage = 1;
        currentEvents = [];
    }

    const container = document.getElementById('events-container');
    if (!isAppend) {
        container.innerHTML = skeletonCardsHtml();
    }

    // Build Query String
    const params = new URLSearchParams();
    params.set('page', currentPage);
    params.set('limit', pageSize);

    const category = document.getElementById('filter-category').value;
    if (category && category !== 'all') params.set('type', category);

    const shopId = document.getElementById('filter-shop').value;
    if (shopId) params.set('shop_id', shopId);

    const camId = document.getElementById('filter-camera').value;
    if (camId) params.set('camera_id', camId);

    const minConf = parseFloat(document.getElementById('filter-min-conf').value);
    if (minConf > 0) params.set('min_confidence', minConf);

    // Date Logic
    const preset = document.getElementById('filter-date-preset').value;
    const now = new Date();
    
    if (preset === 'today') {
        params.set('date_str', now.toISOString().split('T')[0]);
    } else if (preset === 'yesterday') {
        const yest = new Date(now);
        yest.setDate(yest.getDate() - 1);
        params.set('date_str', yest.toISOString().split('T')[0]);
    } else if (preset === '7days') {
        const past = new Date(now);
        past.setDate(past.getDate() - 7);
        params.set('start_date', past.toISOString().split('T')[0]);
        params.set('end_date', now.toISOString().split('T')[0]);
    } else if (preset === 'custom') {
        const cDate = document.getElementById('filter-custom-date').value;
        if (cDate) params.set('date_str', cDate);
    }

    try {
        const res = await fetch(`${API_BASE}/dashboard/detections?${params.toString()}`);
        const data = await res.json();
        
        const items = data.items || [];
        totalEvents = data.total || items.length;
        document.getElementById('events-count-badge').textContent = `${totalEvents} Events`;

        if (isAppend) {
            currentEvents = currentEvents.concat(items);
        } else {
            currentEvents = items;
        }

        renderEventCards(currentEvents);

        // Load More button visibility
        const loadMoreBtn = document.getElementById('load-more-btn');
        if (currentEvents.length < totalEvents) {
            loadMoreBtn.style.display = 'inline-block';
        } else {
            loadMoreBtn.style.display = 'none';
        }
    } catch (e) {
        console.error("Failed to load events", e);
        if (!isAppend) {
            container.innerHTML = '<p style="color:var(--text-secondary); padding:20px; text-align:center; grid-column: 1/-1;">Failed to load events.</p>';
        }
    }
}

function loadMoreEvents() {
    currentPage++;
    loadEvents(true);
}

function renderEventCards(events) {
    const container = document.getElementById('events-container');
    if (!events.length) {
        container.innerHTML = `
            <div style="grid-column: 1/-1; text-align:center; padding:60px 20px; color:var(--text-secondary);">
                <div style="font-size:3rem; margin-bottom:12px;">📸</div>
                <h3 style="margin:0 0 6px; color:var(--text-primary);">No Detection Events Found</h3>
                <p style="margin:0; font-size:0.9rem;">Try selecting a different date range, shop, or category filter.</p>
            </div>`;
        return;
    }

    container.innerHTML = events.map(ev => {
        const meta = getCategoryMeta(ev.detection_type);
        const hasSnap = !!ev.snapshot_path;
        const snapSrc = hasSnap ? ev.snapshot_path : '';
        const confPct = ev.confidence ? Math.round(ev.confidence * 100) : null;
        const timeStr = formatEventTime(ev.detected_at);

        return `
            <div class="event-card" onclick="openSnapshotModal('${ev.id}')">
                <div class="event-thumb-wrap">
                    ${hasSnap 
                        ? `<img src="${snapSrc}" alt="Detection" class="event-thumb" loading="lazy" onerror="this.parentElement.innerHTML='<span class=\\'event-thumb-placeholder\\'>📸</span>'">`
                        : `<span class="event-thumb-placeholder"><i class="fa-solid ${meta.icon}"></i></span>`
                    }
                    <span class="event-tag-badge" style="color:${meta.color}; border-color:${meta.color}40;">
                        <i class="fa-solid ${meta.icon}"></i> ${meta.label}
                    </span>
                    ${confPct ? `<span class="event-conf-badge">${confPct}%</span>` : ''}
                </div>
                <div class="event-body">
                    <div class="event-title">
                        <span>📹 ${esc(ev.camera_name || 'Camera')}</span>
                        ${ev.track_id !== null && ev.track_id !== undefined ? `<span style="font-size:0.75rem; color:var(--text-secondary);">ID #${ev.track_id}</span>` : ''}
                    </div>
                    <div class="event-meta">
                        <span>🏪 ${esc(ev.shop_name || 'Shop')}</span>
                        <span>⏱️ ${timeStr}</span>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function skeletonCardsHtml() {
    return Array.from({ length: 6 }).map(() => `
        <div class="event-card" style="opacity:0.6;">
            <div class="event-thumb-wrap">
                <div class="skeleton-line" style="width:100%; height:100%;"></div>
            </div>
            <div class="event-body">
                <div class="skeleton-line" style="width:60%; height:16px;"></div>
                <div class="skeleton-line" style="width:40%; height:12px; margin-top:6px;"></div>
            </div>
        </div>
    `).join('');
}

// ── Snapshot Modal Lightbox ──────────────────────────────────────────────────
function openSnapshotModal(eventId) {
    const ev = currentEvents.find(e => String(e.id) === String(eventId));
    if (!ev) return;

    const meta = getCategoryMeta(ev.detection_type);
    document.getElementById('modal-event-title').innerHTML = `
        <i class="fa-solid ${meta.icon}" style="color:${meta.color};"></i>
        ${meta.label} Detection — ${esc(ev.camera_name || 'Camera')}
    `;

    const imgEl = document.getElementById('modal-img');
    const dlLink = document.getElementById('modal-download-link');

    if (ev.snapshot_path) {
        imgEl.src = ev.snapshot_path;
        imgEl.style.display = 'block';
        dlLink.href = ev.snapshot_path;
        dlLink.style.display = 'inline-flex';
    } else {
        imgEl.src = '';
        imgEl.style.display = 'none';
        dlLink.style.display = 'none';
    }

    document.getElementById('modal-meta-loc').textContent = `${ev.shop_name || 'Shop'} • ${ev.camera_name || 'Camera'}`;
    document.getElementById('modal-meta-time').textContent = formatEventTime(ev.detected_at, true);

    document.getElementById('snapshot-modal').classList.add('active');
}

function closeSnapshotModal() {
    document.getElementById('snapshot-modal').classList.remove('active');
}

// ── Helpers ──────────────────────────────────────────────────────────────────
function formatEventTime(iso, full = false) {
    if (!iso) return '—';
    try {
        const d = new Date(iso);
        if (full) {
            return d.toLocaleDateString() + ' at ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        }
        return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
        return iso;
    }
}

function esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
