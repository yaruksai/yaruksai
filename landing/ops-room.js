/**
 * YARUKSAİ — Operasyon Odası Controller
 * ═══════════════════════════════════════
 * No Fluff. Anlık. Deterministik.
 */

const API = window.location.origin;
const SIGMA_LIMIT = 0.30;
let runId = null;
let sse = null;
let logN = 0;
const startTime = Date.now();

// ─── Clock + Uptime ────────────────────────────────
function tick() {
    const n = new Date();
    const p = (v) => String(v).padStart(2, '0');
    document.getElementById('sys-clock').textContent =
        `${p(n.getHours())}:${p(n.getMinutes())}:${p(n.getSeconds())}`;
    const up = Math.floor((Date.now() - startTime) / 1000);
    const h = Math.floor(up / 3600), m = Math.floor((up % 3600) / 60), s = up % 60;
    document.getElementById('hdr-uptime').textContent = `${p(h)}:${p(m)}:${p(s)}`;
}
setInterval(tick, 1000);
tick();

// ─── Health ─────────────────────────────────────────
async function health() {
    try {
        const r = await fetch(`${API}/api/health`);
        const d = await r.json();
        document.getElementById('hdr-status').textContent = d.status === 'ok' ? 'ACTIVE' : 'DOWN';
        document.getElementById('hdr-status').style.color = d.status === 'ok' ? 'var(--green)' : 'var(--red)';
        document.getElementById('ri-engine').textContent = d.engine || '—';
        document.getElementById('ri-llm').textContent = d.llm?.provider || '—';
    } catch {
        document.getElementById('hdr-status').textContent = 'OFFLINE';
        document.getElementById('hdr-status').style.color = 'var(--red)';
    }
}
setInterval(health, 20000);
health();

// ─── Sigma ──────────────────────────────────────────
function setSigma(v, votes) {
    const circ = 2 * Math.PI * 88;
    const off = circ - (v * circ);
    const fill = document.getElementById('sigma-fill');
    fill.style.strokeDashoffset = off;

    const vd = document.getElementById('sigma-val');
    vd.textContent = v.toFixed(4);

    const verdict = document.getElementById('sigma-verdict');
    const kill = document.getElementById('kill-switch');

    if (v >= SIGMA_LIMIT) {
        fill.classList.remove('fail');
        vd.style.color = 'var(--green)';
        verdict.textContent = 'MİZAN ONAYLANDI · PİPELİNE BAŞLATILIYOR';
        verdict.className = 'ok';
        kill.classList.add('hidden');
    } else {
        fill.classList.add('fail');
        vd.style.color = 'var(--red)';
        verdict.textContent = 'İŞLEM REDDEDİLDİ · YETERSİZ MİZAN';
        verdict.className = 'fail';
        kill.classList.remove('hidden');
        setTimeout(() => kill.classList.add('hidden'), 4000);
    }

    // Eğer votes yoksa, sigma'dan türetilmiş varsayılan scores üret
    const agentNames = ['TEVHID', 'ADALET', 'MERHAMET', 'EMANET', 'IHSAN', 'SIDK', 'MIZAN'];
    const voteData = votes && votes.length ? votes : agentNames.map((name, i) => ({
        agent: name,
        score: Math.max(0.1, Math.min(1.0, v + (Math.sin(i * 1.7) * 0.15))),
    }));

    voteData.forEach(vo => {
        const row = document.querySelector(`.vote[data-agent="${vo.agent}"]`);
        if (!row) return;
        const score = vo.score || 0;
        row.querySelector('.v-fill').style.width = `${score * 100}%`;
        row.querySelector('.v-sc').textContent = score.toFixed(2);
    });
}

// ─── Log ────────────────────────────────────────────
function log(stage, type, msg) {
    const f = document.getElementById('log-feed');
    const t = new Date();
    const ts = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}:${String(t.getSeconds()).padStart(2, '0')}`;
    const cls = type === 'ok' ? 'log-ok' : type === 'err' ? 'log-err' : 'log-info';
    const div = document.createElement('div');
    div.innerHTML = `<span class="log-t">${ts}</span> <span class="log-s">[${stage}]</span> <span class="${cls}">${msg}</span>`;
    f.appendChild(div);
    f.scrollTop = f.scrollHeight;
    logN++;
}

// ─── Stage Control ──────────────────────────────────
function resetStages() {
    document.querySelectorAll('.stage').forEach(s => {
        s.classList.remove('active', 'sealed', 'failed');
        s.querySelector('.stage-detail').textContent = 'Bekleniyor';
    });
}

function stageActive(name) {
    const el = document.querySelector(`.stage[data-stage="${name}"]`);
    if (!el) return;
    document.querySelectorAll('.stage.active').forEach(s => s.classList.remove('active'));
    el.classList.add('active');
    el.querySelector('.stage-detail').textContent = 'Çalışıyor...';
}

function stageSealed(name, detail) {
    const el = document.querySelector(`.stage[data-stage="${name}"]`);
    if (!el) return;
    el.classList.remove('active');
    el.classList.add('sealed');
    el.querySelector('.stage-detail').textContent = detail || 'Tamamlandı';
}

function stageFailed(name, detail) {
    const el = document.querySelector(`.stage[data-stage="${name}"]`);
    if (!el) return;
    el.classList.remove('active');
    el.classList.add('failed');
    el.querySelector('.stage-detail').textContent = detail || 'Başarısız';
}

// ─── Pipeline Run ───────────────────────────────────
document.getElementById('btn-run').addEventListener('click', run);

async function run() {
    const inp = document.getElementById('goal-input');
    const goal = inp.value.trim();
    if (!goal) { inp.style.outline = '1px solid var(--red)'; setTimeout(() => inp.style.outline = '', 1000); return; }

    const btn = document.getElementById('btn-run');
    btn.disabled = true;
    btn.textContent = '⏳ COUNCIL + PİPELİNE...';
    resetStages();
    document.getElementById('log-feed').innerHTML = '';
    logN = 0;
    document.getElementById('artifact-tree').innerHTML = '';
    document.getElementById('raw-viewer').textContent = '';

    // 30 saniye timeout
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 30000);

    try {
        const res = await fetch(`${API}/api/pipeline/trigger`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            signal: ctrl.signal,
            body: JSON.stringify({
                goal,
                raw_metrics: {
                    fayda: 0.85,
                    seffaflik: 'FULL_SYNC',
                    sozlesme: 'ACTIVE',
                    mucbir_sebep: 'NONE',
                    israf: 0.1,
                },
                narrative: goal,
                action_id: `ops-${Date.now()}`,
            })
        });
        clearTimeout(timer);

        let data;
        try { data = await res.json(); } catch { data = { status: 'error', reason: 'JSON parse hatası' }; }

        // Sigma ve votes güncelle
        if (data.sigma !== undefined) {
            setSigma(data.sigma, data.votes || []);
        }

        // Red durumu (σ < 0.30)
        if (data.status === 'rejected' || res.status === 403) {
            log('COUNCIL', 'err', data.reason || 'İŞLEM REDDEDİLDİ');
            btn.disabled = false; btn.textContent = '▶ ÇALIŞTIR'; return;
        }

        // Pipeline meşgul veya hata
        if (data.status === 'error') {
            log('SYS', 'err', data.reason || 'Pipeline hatası');
            btn.disabled = false; btn.textContent = '▶ ÇALIŞTIR'; return;
        }

        // Pipeline başladı
        if (data.status === 'pipeline_started' && data.run_id) {
            runId = data.run_id;
            document.getElementById('ri-runid').textContent = runId;
            document.getElementById('ri-status').textContent = 'RUNNING';
            log('COUNCIL', 'ok', `σ=${data.sigma?.toFixed(4)} → ${data.verdict} → Pipeline: ${runId}`);
            connectSSE(data.stream_url);
        } else {
            log('SYS', 'err', `Beklenmeyen yanıt: ${JSON.stringify(data)}`);
            btn.disabled = false; btn.textContent = '▶ ÇALIŞTIR';
        }
    } catch (e) {
        clearTimeout(timer);
        const msg = e.name === 'AbortError' ? 'Zaman aşımı (30s)' : e.message;
        log('SYS', 'err', `Bağlantı hatası: ${msg}`);
        btn.disabled = false; btn.textContent = '▶ ÇALIŞTIR';
    }
}

// ─── SSE ────────────────────────────────────────────
function connectSSE(url) {
    if (sse) sse.close();
    sse = new EventSource(`${API}${url}`);

    sse.addEventListener('stage_started', e => {
        const d = JSON.parse(e.data);
        stageActive(d.stage);
        log(d.stage, 'info', `${d.stage.toUpperCase()} başladı`);
    });

    sse.addEventListener('stage_completed', e => {
        const d = JSON.parse(e.data);
        let extra = '';
        if (d.review_decision) extra = d.review_decision;
        if (d.decision) extra = d.decision;
        stageSealed(d.stage, extra || 'Tamamlandı');
        log(d.stage, 'ok', `${d.stage.toUpperCase()} SEALED${extra ? ' → ' + extra : ''}`);
    });

    sse.addEventListener('completed', e => {
        log('SYS', 'ok', '✅ Pipeline tamamlandı');
        finish('ok');
        loadArtifacts();
    });

    sse.addEventListener('failed', e => {
        const d = JSON.parse(e.data);
        log('SYS', 'err', `❌ HATA: ${d.error || 'bilinmeyen'}`);
        finish('err');
    });

    sse.onerror = () => {
        log('SYS', 'err', 'SSE bağlantısı kesildi');
        // Auto-reconnect'i engelle — döngü olmasın
        if (sse) { sse.close(); sse = null; }
        finish('err');
    };
}

function finish(status) {
    const btn = document.getElementById('btn-run');
    btn.disabled = false;
    btn.textContent = '▶ ÇALIŞTIR';
    document.getElementById('ri-status').textContent = status === 'ok' ? 'COMPLETED' : 'FAILED';
    if (sse) { sse.close(); sse = null; }
}

// ─── Artifacts ──────────────────────────────────────
async function loadArtifacts() {
    if (!runId) return;
    try {
        const r = await fetch(`${API}/api/pipeline/artifacts/${runId}`);
        const d = await r.json();
        const tree = document.getElementById('artifact-tree');
        tree.innerHTML = '';
        (d.files || []).forEach(f => {
            const item = document.createElement('div');
            item.className = 'af-item';
            const ext = f.split('.').pop();
            const ico = ext === 'json' ? '📋' : ext === 'txt' ? '📄' : '📁';
            item.innerHTML = `${ico} ${f}`;
            item.onclick = () => viewArtifact(f);
            tree.appendChild(item);
        });
        // Hakikat Paketi butonu göster
        if ((d.files || []).length > 0) {
            document.getElementById('btn-download').classList.remove('hidden');
        }
    } catch { /* silent */ }
}

function downloadZip() {
    if (!runId) return;
    window.open(`${API}/api/pipeline/artifacts/${runId}/download`, '_blank');
}

async function viewArtifact(filename) {
    if (!runId) return;
    try {
        const r = await fetch(`${API}/api/pipeline/artifacts/${runId}/${filename}`);
        const text = await r.text();
        const viewer = document.getElementById('raw-viewer');
        try {
            viewer.textContent = JSON.stringify(JSON.parse(text), null, 2);
        } catch {
            viewer.textContent = text;
        }
    } catch (e) {
        document.getElementById('raw-viewer').textContent = `Hata: ${e.message}`;
    }
}
