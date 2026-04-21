// ─────────────── State ───────────────
let students = [];
let currentFilter = 'all';
let searchQuery = '';
let activityLog = [];
let pollInterval = null;

const API = '';  // Same origin

// ─────────────── Init ───────────────
document.addEventListener('DOMContentLoaded', () => {
    loadStats();
    loadStudents();
    loadHumanReview();
    // Auto-refresh every 5 seconds
    pollInterval = setInterval(() => {
        loadStudents();
        loadStats();
    }, 5000);
});

// ─────────────── API Calls ───────────────
async function loadStats() {
    try {
        const res = await fetch(`${API}/api/stats`);
        const data = await res.json();
        document.getElementById('statTotal').textContent = data.total;
        document.getElementById('statCompleted').textContent = data.completed;
        document.getElementById('statPending').textContent = data.pending;
        document.getElementById('statPartial').textContent = data.partial;
        document.getElementById('statReview').textContent = data.human_review;
        document.getElementById('statRate').textContent = data.completion_rate + '%';
    } catch (e) {
        console.error('Stats load failed:', e);
    }
}

async function loadStudents() {
    try {
        const res = await fetch(`${API}/api/students`);
        const data = await res.json();
        students = data.students;
        renderStudentTable();
    } catch (e) {
        console.error('Students load failed:', e);
        document.getElementById('studentTableBody').innerHTML = 
            '<tr><td colspan="12" class="loading-cell">Failed to connect to server. Is the backend running?</td></tr>';
    }
}

async function loadHumanReview() {
    try {
        const res = await fetch(`${API}/api/human-review`);
        const data = await res.json();
        renderHumanReview(data.reviews);
        document.getElementById('reviewBadge').textContent = data.total;
    } catch (e) {
        console.error('Human review load failed:', e);
    }
}

async function triggerOutreach(studentId) {
    try {
        const res = await fetch(`${API}/api/students/${studentId}/trigger`, { method: 'POST' });
        const data = await res.json();

        if (res.ok) {
            showToast(`Outreach started for ${data.student_id}`, 'success');
            addLogEntry(`Triggered WhatsApp outreach for ${studentId}`);
            loadStudents();
        } else {
            showToast(data.detail || 'Failed to trigger', 'error');
        }
    } catch (e) {
        showToast('Connection error', 'error');
    }
}

async function runDailyJob() {
    const btn = document.getElementById('dailyJobBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-icon">⏳</span> Running...';

    try {
        const res = await fetch(`${API}/api/jobs/daily-check`, { method: 'POST' });
        const data = await res.json();

        showToast(data.message, 'success');
        addLogEntry(`Daily check: triggered ${data.triggered.length} outreach(es), skipped ${data.skipped.length}`);

        if (data.triggered.length > 0) {
            addLogEntry(`Triggered students: ${data.triggered.join(', ')}`);
        }

        loadStudents();
        loadStats();
    } catch (e) {
        showToast('Daily check failed', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<span class="btn-icon">⚡</span> Run Daily Check';
    }
}

async function viewConversation(studentId, studentName) {
    try {
        const res = await fetch(`${API}/api/conversations/${studentId}`);
        if (!res.ok) {
            showToast('No conversation log found', 'info');
            return;
        }
        const data = await res.json();
        showConversationModal(studentName, data);
    } catch (e) {
        showToast('Failed to load conversation', 'error');
    }
}

// ─────────────── Rendering ───────────────
function renderStudentTable() {
    const tbody = document.getElementById('studentTableBody');
    let filtered = students;

    // Filter
    if (currentFilter !== 'all') {
        filtered = filtered.filter(s => s.status === currentFilter);
    }

    // Search
    if (searchQuery) {
        const q = searchQuery.toLowerCase();
        filtered = filtered.filter(s =>
            s.student_name.toLowerCase().includes(q) ||
            s.parent_name.toLowerCase().includes(q) ||
            s.student_id.toLowerCase().includes(q) ||
            (s.parent_email || '').toLowerCase().includes(q) ||
            (s.school_name || '').toLowerCase().includes(q)
        );
    }

    if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="12" class="loading-cell">No students match your filter.</td></tr>';
        return;
    }

    tbody.innerHTML = filtered.map(s => {
        const isComplete = s.is_complete;
        const isRunning = s.job_status === 'running';
        const statusClass = isRunning ? 'running' : s.status;

        let actionBtn = '';
        if (isComplete) {
            actionBtn = `<button class="btn-trigger" disabled>✅ Done</button>`;
        } else if (isRunning) {
            actionBtn = `<button class="btn-trigger btn-running">
                <span class="spinner">⏳</span> Running
            </button>`;
        } else if (s.status === 'opt_out') {
            actionBtn = `<button class="btn-trigger" disabled>🚫 Opted Out</button>`;
        } else {
            actionBtn = `<button class="btn-trigger btn-start" onclick="triggerOutreach('${s.student_id}')">
                📲 Collect
            </button>`;
        }

        return `<tr>
            <td class="student-name">${s.student_name}</td>
            <td class="cell-filled">${s.grade}</td>
            <td class="cell-filled">${s.parent_name}</td>
            <td class="cell-filled">${formatPhone(s.parent_phone)}</td>
            <td class="${s.parent_email ? 'cell-filled' : 'cell-empty'}">${s.parent_email || '—'}</td>
            <td class="${s.school_name ? 'cell-filled' : 'cell-empty'}">${s.school_name || '—'}</td>
            <td class="${s.preferred_weekday ? 'cell-filled' : 'cell-empty'}">${s.preferred_weekday || '—'}</td>
            <td class="${s.preferred_time ? 'cell-filled' : 'cell-empty'}">${s.preferred_time || '—'}</td>
            <td class="${s.notes ? 'cell-filled' : 'cell-empty'}" title="${s.notes || ''}">${s.notes || '—'}</td>
            <td><span class="status-badge status-${statusClass}">${formatStatus(statusClass)}</span></td>
            <td class="cell-filled">${s.attempts}/3</td>
            <td>${actionBtn}</td>
        </tr>`;
    }).join('');
}

function renderHumanReview(reviews) {
    const container = document.getElementById('reviewList');

    if (reviews.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <span class="empty-icon">✅</span>
                <p>No conversations need human review right now.</p>
            </div>`;
        return;
    }

    container.innerHTML = reviews.map(r => `
        <div class="review-card">
            <div class="review-header">
                <div class="review-info">
                    <h3>${r.student_name} — ${r.parent_name}</h3>
                    <p>Phone: ${r.parent_phone} · Attempts: ${r.attempts}/3 · Last: ${r.last_contact || 'Never'}</p>
                </div>
                <div class="review-actions">
                    <button class="btn-sm" onclick="viewConversation('${r.student_id}', '${r.student_name}')">
                        💬 View Chat
                    </button>
                    <button class="btn-sm" onclick="triggerOutreach('${r.student_id}')">
                        🔄 Retry
                    </button>
                </div>
            </div>
            <div class="missing-tags">
                ${r.missing_fields.map(f => `<span class="missing-tag">${formatField(f)}</span>`).join('')}
            </div>
        </div>
    `).join('');
}

// ─────────────── Modal ───────────────
function showConversationModal(studentName, data) {
    document.getElementById('modalTitle').textContent = `Chat: ${studentName}`;

    const conv = data.conversation || [];
    const result = data.result || {};

    let html = '';
    if (conv.length === 0) {
        html = '<div class="empty-state"><p>No conversation messages recorded.</p></div>';
    } else {
        html = conv.map(msg => `
            <div class="chat-bubble ${msg.role === 'agent' ? 'chat-agent' : 'chat-parent'}">
                ${escapeHtml(msg.content)}
            </div>
        `).join('');
    }

    if (result.status) {
        html += `
            <div style="margin-top: 16px; padding: 12px; background: var(--bg-secondary); border-radius: 8px; font-size: 12px; color: var(--text-muted);">
                <strong>Result:</strong> ${result.status} · Confidence: ${result.confidence || 'N/A'}
                ${result.notes ? `<br><strong>Notes:</strong> ${result.notes}` : ''}
            </div>`;
    }

    document.getElementById('modalBody').innerHTML = html;
    document.getElementById('modalOverlay').classList.add('visible');
}

function closeModal() {
    document.getElementById('modalOverlay').classList.remove('visible');
}

// ─────────────── UI Helpers ───────────────
function switchTab(tabName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.querySelector(`[data-tab="${tabName}"]`).classList.add('active');
    document.getElementById(`tab-${tabName}`).classList.add('active');

    if (tabName === 'review') loadHumanReview();
}

function filterStudents(query) {
    searchQuery = query;
    renderStudentTable();
}

function setFilter(filter) {
    currentFilter = filter;
    document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
    event.target.classList.add('active');
    renderStudentTable();
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    const icons = { success: '✅', error: '❌', info: 'ℹ️' };
    toast.innerHTML = `<span>${icons[type] || 'ℹ️'}</span> ${escapeHtml(message)}`;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

function addLogEntry(message) {
    const time = new Date().toLocaleTimeString('en-US', { hour12: false });
    activityLog.unshift({ time, message });

    const container = document.getElementById('logList');
    container.innerHTML = activityLog.slice(0, 50).map(l => `
        <div class="log-entry">
            <span class="log-time">${l.time}</span>
            <span class="log-message">${escapeHtml(l.message)}</span>
        </div>
    `).join('');
}

// ─────────────── Formatters ───────────────
function formatPhone(phone) {
    if (!phone) return '—';
    return String(phone).replace('whatsapp:', '').replace(/^\+91/, '+91-');
}

function formatStatus(status) {
    const map = {
        completed: '✅ Complete',
        pending: '🟡 Pending',
        partial: '🟠 Partial',
        human_review: '🔴 Review',
        opt_out: '🚫 Opted Out',
        running: '🔵 Running',
        unreachable: '⚪ Unreachable',
        wrong_number: '❌ Wrong #',
        callback_requested: '📞 Callback',
    };
    return map[status] || status;
}

function formatField(field) {
    return field.replace('parent_', '').replace(/_/g, ' ')
        .replace(/\b\w/g, l => l.toUpperCase());
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
