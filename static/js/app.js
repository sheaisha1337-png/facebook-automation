/* ================================================================
   Facebook Automation — app.js  v12
   ================================================================ */
'use strict';

const state = {
    statsFiles: 0,
    statsClips: 0,
};

function $(id)   { return document.getElementById(id); }
function $q(sel) { return document.querySelector(sel); }

let toastTimeout = null;

function showToast(msg, type = 'success', duration = 4000) {
    const t = $('toast');
    if (!t) return;

    if (toastTimeout) {
        clearTimeout(toastTimeout);
        toastTimeout = null;
    }

    t.innerHTML = `
        <span class="toast-content">${msg}</span>
        <button type="button" class="toast-close" id="toast-close-btn" title="Dismiss Alert">&times;</button>
    `;
    t.className = `toast ${type} show`;

    t.querySelector('#toast-close-btn')?.addEventListener('click', () => {
        t.classList.remove('show');
    });

    // Keep error toasts visible until explicitly dismissed
    if (type !== 'error') {
        toastTimeout = setTimeout(() => {
            t.classList.remove('show');
        }, duration);
    }
}

function animateCount(el, target) {
    if (!el) return;
    const start = parseInt(el.textContent) || 0;
    const diff  = target - start;
    if (diff === 0) return;
    const step  = Math.ceil(Math.abs(diff) / 10) || 1;
    const dir   = diff > 0 ? 1 : -1;
    let cur = start;
    const iv = setInterval(() => {
        cur += dir * step;
        if ((dir > 0 && cur >= target) || (dir < 0 && cur <= target)) {
            cur = target;
            clearInterval(iv);
        }
        el.textContent = cur;
    }, 30);
}

// ─────────────────────────────────────────────────────────────────
// GALLERY / LIBRARY
// ─────────────────────────────────────────────────────────────────
async function loadGallery() {
    const container = $('gallery-container');
    if (!container) return;

    const searchVal = ($('library-search')?.value || '').toLowerCase();

    try {
        const res  = await fetch('/api/outputs');
        const data = await res.json();

        const filtered = searchVal
            ? data.filter(f => f.filename.toLowerCase().includes(searchVal))
            : data;

        if (!filtered.length) {
            container.innerHTML = `
                <div class="empty-library">
                    <div class="empty-art">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                            <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/>
                            <line x1="7" y1="2" x2="7" y2="22"/>
                            <line x1="17" y1="2" x2="17" y2="22"/>
                            <line x1="2" y1="12" x2="22" y2="12"/>
                        </svg>
                    </div>
                    <p class="empty-title">${searchVal ? 'No results found' : 'No exported clips'}</p>
                    <p class="empty-desc">${searchVal ? 'Try a different search term.' : 'Run a clipping operation to begin.'}</p>
                </div>`;
            animateCount($('stat-files'), 0);
            return;
        }

        state.statsFiles = data.length;
        animateCount($('stat-files'), data.length);

        container.innerHTML = filtered.map(file => {
            const isClip  = file.filename.includes('clip_');
            const iconSvg = isClip 
                ? `<svg class="card-file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 10a4 4 0 108 0 4 4 0 00-8 0zm0 0V6a4 4 0 018 0v4m-8 0h8" stroke-linecap="round" stroke-linejoin="round"/></svg>`
                : `<svg class="card-file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/><line x1="7" y1="2" x2="7" y2="22"/></svg>`;
            return `
            <div class="video-card" data-fn="${file.filename}">
                <div class="video-card-info">
                    <span class="video-card-title">${iconSvg} ${file.filename}</span>
                    <span class="video-card-meta">${file.duration} &nbsp;·&nbsp; ${file.size}</span>
                </div>
                <div class="video-card-actions">
                    <button class="action-btn play-action" data-fn="${file.filename}">
                        <svg viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                        Play
                    </button>
                    <button class="action-btn download-action" data-fn="${file.filename}">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4m4-5l5 5 5-5m-5 5V3" stroke-linecap="round" stroke-linejoin="round"/></svg>
                    </button>
                </div>
            </div>`;
        }).join('');

        // Wire up play / download
        container.querySelectorAll('.play-action').forEach(btn => {
            btn.addEventListener('click', () => openVideoModal(btn.dataset.fn));
        });
        container.querySelectorAll('.download-action').forEach(btn => {
            btn.addEventListener('click', () => {
                const a = document.createElement('a');
                a.href = `/api/outputs/${btn.dataset.fn}`;
                a.download = btn.dataset.fn;
                a.click();
            });
        });

    } catch (e) {
        console.error('Error loading library:', e);
        container.innerHTML = `<div class="empty-library"><p class="empty-desc">Failed to load library.</p></div>`;
    }
}

// ─────────────────────────────────────────────────────────────────
// CLEAR LIBRARY
// ─────────────────────────────────────────────────────────────────
async function clearLibrary() {
    const confirmed = confirm('🗑️ Delete ALL exported files from disk?\n\nThis cannot be undone.');
    if (!confirmed) return;

    const btn = $('clear-library-btn');
    if (btn) btn.disabled = true;

    try {
        const res  = await fetch('/api/clear-library', { method: 'POST' });
        const data = await res.json();
        showToast(`✅ Cleared ${data.deleted} file${data.deleted !== 1 ? 's' : ''} from library`, 'success');
        animateCount($('stat-files'), 0);
        animateCount($('stat-clips'), 0);
        await loadGallery();
    } catch (e) {
        showToast('❌ Failed to clear library', 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}

// ─────────────────────────────────────────────────────────────────
// VIDEO PLAYER MODAL
// ─────────────────────────────────────────────────────────────────
function openVideoModal(filename) {
    $('modal-title').textContent = `▶ ${filename}`;
    $('modal-player').src = `/api/outputs/${filename}`;
    $('video-modal').classList.add('active');
    $('modal-player').play();
}
function closeVideoModal() {
    $('modal-player').pause();
    $('modal-player').src = '';
    $('video-modal').classList.remove('active');
}

// ─────────────────────────────────────────────────────────────────
// SLICING TOGGLE
// ─────────────────────────────────────────────────────────────────
function initAudioToggle() {
    const clipToggles = document.querySelectorAll('.segment-tab-btn');
    clipToggles.forEach(btn => {
        btn.addEventListener('click', () => {
            clipToggles.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const type = btn.dataset.type;
            $('auto-clip-panel')?.classList.toggle('active', type === 'auto');
            $('timestamps-clip-panel')?.classList.toggle('active', type === 'timestamps');
        });
    });
}

function startProgress(fillId, pctId, stepId, steps, onDone) {
    const fill = $(fillId);
    const pct = $(pctId);
    const step = $(stepId);
    if (!fill) return null;
    let idx = 0, cur = 0, cancelled = false;
    const runStep = () => {
        if (cancelled) return;
        if (idx >= steps.length) { onDone?.(); return; }
        const target = steps[idx].pct, label = steps[idx].label, dur = steps[idx].dur;
        step.textContent = label;
        const startPct = cur, started = Date.now();
        const timer = setInterval(() => {
            const ratio = Math.min((Date.now() - started) / dur, 1);
            cur = startPct + (target - startPct) * ratio;
            fill.style.width = cur + "%";
            pct.textContent = Math.round(cur) + "%";
            if (ratio >= 1) { clearInterval(timer); idx += 1; runStep(); }
        }, 120);
    };
    runStep();
    return () => { cancelled = true; };
}

function showProgress(progressId) { $(progressId)?.classList.add("visible"); }
function hideProgress(progressId) { $(progressId)?.classList.remove("visible"); }
function initClipForm() {
    const form = $('clip-form');
    if (!form) return;

    // Import source tab switching logic
    const sourceToggles = document.querySelectorAll('.source-tabs .segment-tab-btn');
    let activeSource = 'youtube'; // default

    sourceToggles.forEach(btn => {
        btn.addEventListener('click', () => {
            sourceToggles.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            activeSource = btn.dataset.source;
            $('source-panel-youtube')?.classList.toggle('active', activeSource === 'youtube');
            $('source-panel-upload')?.classList.toggle('active', activeSource === 'upload');
        });
    });

    $('video_file')?.addEventListener('change', e => {
        const file = e.target.files[0];
        const label = $('file-name-label');
        if (label) label.textContent = file ? file.name : 'No file selected';
    });

    form.addEventListener('submit', async e => {
        e.preventDefault();

        if (activeSource === 'youtube') {
            const url = $('url')?.value.trim();
            if (!url || (!url.includes('youtube.com') && !url.includes('youtu.be'))) {
                showToast('⚠️ Please enter a valid YouTube URL', 'error'); return;
            }
        } else {
            const fileInput = $('video_file');
            if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
                showToast('⚠️ Please select a local video file to upload', 'error'); return;
            }
        }

        const btn = $('clip-submit-btn');
        btn.disabled = true; btn.classList.add('loading');
        showProgress('clip-progress');

        const profile = $('processing_profile')?.value || 'balanced';
        const profileLabel = { turbo: 'Turbo 720p', balanced: 'Balanced 1080p', quality: 'High Quality 1080p' }[profile];
        const multiplier = { turbo: 0.65, balanced: 1, quality: 1.6 }[profile] || 1;
        const steps = [
            { pct: 18, label: activeSource === 'youtube' ? '⬇️ Downloading source…' : '⬆️ Uploading source…', dur: 3000 },
            { pct: 38, label: '✂️ Creating source slices…', dur: 2200 },
            { pct: 82, label: '⚙️ ' + profileLabel + ' · encoding clips…', dur: 6500 * multiplier },
            { pct: 94, label: $('parallel_processing')?.checked ? '🚀 Finalizing parallel renders…' : '💾 Finalizing renders…', dur: 1800 },
            { pct: 99, label: '📦 Validating output files…', dur: 900 },
        ];
        startProgress('clip-fill', 'clip-pct', 'clip-step', steps);

        try {
            const fd  = new FormData(form);
            const activeMethodBtn = $q('#clip-toggle-auto.active, #clip-toggle-ts.active');
            if (activeMethodBtn) {
                fd.set('slicing_method', activeMethodBtn.dataset.type);
            }
            // Explicitly set source type
            fd.set('import_source', activeSource);
            
            const res = await fetch('/api/clip', { method: 'POST', body: fd });
            const data = await res.json();

            $('clip-fill').style.width = '100%';
            $('clip-pct').textContent  = '100%';
            $('clip-step').textContent = '✅ Done!';

            if (data.success) {
                showToast(`✅ ${data.message}`, 'success', 5000);
                state.statsClips += data.filenames?.length || 0;
                animateCount($('stat-clips'), state.statsClips);
                await loadGallery();
            } else {
                showToast(`❌ ${data.error}`, 'error', 7000);
            }
        } catch (err) {
            showToast('❌ Network error — check server logs', 'error');
        } finally {
            btn.disabled = false; btn.classList.remove('loading');
            setTimeout(() => hideProgress('clip-progress'), 2000);
        }
    });
}

// ─────────────────────────────────────────────────────────────────
// THEME SWITCHER
// ─────────────────────────────────────────────────────────────────
function initThemeSwitcher() {
    const toggleBtn = $('theme-toggle');
    if (!toggleBtn) return;

    const sunIcon = toggleBtn.querySelector('.sun-icon');
    const moonIcon = toggleBtn.querySelector('.moon-icon');

    // Load initial theme from localStorage
    const savedTheme = localStorage.getItem('theme') || 'dark';
    if (savedTheme === 'light') {
        document.body.classList.add('light-theme');
        if (sunIcon) sunIcon.style.display = 'none';
        if (moonIcon) moonIcon.style.display = 'block';
    }

    toggleBtn.addEventListener('click', () => {
        const isLight = document.body.classList.toggle('light-theme');
        localStorage.setItem('theme', isLight ? 'light' : 'dark');

        if (isLight) {
            if (sunIcon) sunIcon.style.display = 'none';
            if (moonIcon) moonIcon.style.display = 'block';
            showToast('☀️ Switched to Light theme', 'success', 2000);
        } else {
            if (sunIcon) sunIcon.style.display = 'block';
            if (moonIcon) moonIcon.style.display = 'none';
            showToast('🌙 Switched to Dark theme', 'success', 2000);
        }
    });
}

// ─────────────────────────────────────────────────────────────────
// INITIALIZATION
// ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initThemeSwitcher();
    initAudioToggle();
    initClipForm();

    $('modal-close')?.addEventListener('click', closeVideoModal);
    $('modal-close-btn')?.addEventListener('click', closeVideoModal);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeVideoModal(); });

    // Ratio card visual selection
    document.querySelectorAll('.aspect-card input[type=radio]').forEach(radio => {
        radio.addEventListener('change', () => {
            document.querySelectorAll('.aspect-card').forEach(c => c.classList.remove('selected'));
            radio.closest('.aspect-card').classList.add('selected');
        });
        if (radio.checked) radio.closest('.aspect-card').classList.add('selected');
    });

    $('refresh-gallery')?.addEventListener('click', loadGallery);
    $('download-all-btn')?.addEventListener('click', () => {
        window.location.href = '/api/download-all';
    });
    $('clear-library-btn')?.addEventListener('click', clearLibrary);
    $('library-search')?.addEventListener('input', loadGallery);

    loadGallery();

    // Auto-refresh gallery list every 30 seconds
    setInterval(loadGallery, 30000);
});
