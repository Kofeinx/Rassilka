const API = {
    getConfig:    () => fetch('/api/config').then(r => r.json()),
    addAccount:   (email, password) => fetch('/api/accounts', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email, password})
    }).then(r => r.json()),
    delAccount:   (idx) => fetch(`/api/accounts/${idx}`, {method: 'DELETE'}).then(r => r.json()),
    saveMessage:  (message) => fetch('/api/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message})
    }).then(r => r.json()),
    send:   () => fetch('/api/send', {method: 'POST'}).then(r => r.json()),
    stop:   () => fetch('/api/send/stop', {method: 'POST'}).then(r => r.json()),
    status: () => fetch('/api/status').then(r => r.json()),
};

let config = null;
let pollTimer = null;
let lastFinished = null;

async function loadConfig() {
    config = await API.getConfig();
    renderAccounts();
    renderMessage();
    renderSummary();
}

function renderAccounts() {
    const list  = document.getElementById('accounts-list');
    const count = document.getElementById('accounts-count');
    const accs  = config.accounts || [];
    const real  = accs.filter(a => !a.password.startsWith('REPLACE_'));

    count.textContent = `Всего: ${accs.length} · реальных: ${real.length}`;

    list.innerHTML = '';
    if (!accs.length) {
        list.innerHTML = '<div class="muted">Пока нет ни одного аккаунта. Добавьте первый ↓</div>';
        return;
    }
    accs.forEach((a, i) => {
        const ph = a.password.startsWith('REPLACE_');
        const div = document.createElement('div');
        div.className = 'account-item' + (ph ? ' placeholder' : '');
        div.innerHTML = `
            <span class="email">${a.email}${ph ? ' · заглушка' : ''}</span>
            <button class="del" data-idx="${i}" aria-label="Удалить">✕</button>
        `;
        list.appendChild(div);
    });
    list.querySelectorAll('.del').forEach(btn => {
        btn.onclick = async () => {
            const idx = +btn.dataset.idx;
            if (!confirm(`Удалить ${config.accounts[idx].email}?`)) return;
            const res = await API.delAccount(idx);
            if (res.ok) await loadConfig();
            else alert(res.error || 'Ошибка');
        };
    });
}

function renderMessage() {
    const m = config.message || {};
    document.getElementById('msg-to').value        = m.to || '';
    document.getElementById('msg-subject').value   = m.subject || '';
    document.getElementById('msg-body').value      = m.body || '';
    document.getElementById('delay-min').value     = m.delay_min_sec ?? 15;
    document.getElementById('delay-max').value     = m.delay_max_sec ?? 45;
    document.getElementById('batch-size').value    = m.batch_size ?? 10;
    document.getElementById('batch-pause').value   = m.batch_pause_sec ?? 300;
}

function renderSummary() {
    const real = (config.accounts || []).filter(a => !a.password.startsWith('REPLACE_')).length;
    const to   = config.message?.to || '(не задано)';
    document.getElementById('send-summary').innerHTML =
        `Аккаунтов к отправке: <b>${real}</b><br>Получатель: <b>${to}</b>`;
}

document.getElementById('btn-add').onclick = async () => {
    const email    = document.getElementById('new-email').value.trim();
    const password = document.getElementById('new-password').value.trim();
    if (!email || !password) return alert('Заполните email и пароль');
    const res = await API.addAccount(email, password);
    if (!res.ok) return alert(res.error || 'Ошибка');
    document.getElementById('new-email').value = '';
    document.getElementById('new-password').value = '';
    await loadConfig();
};

document.getElementById('btn-save-msg').onclick = async () => {
    const message = {
        to:               document.getElementById('msg-to').value.trim(),
        subject:          document.getElementById('msg-subject').value,
        body:             document.getElementById('msg-body').value,
        delay_min_sec:    parseFloat(document.getElementById('delay-min').value) || 15,
        delay_max_sec:    parseFloat(document.getElementById('delay-max').value) || 45,
        batch_size:       parseInt(document.getElementById('batch-size').value) || 10,
        batch_pause_sec:  parseFloat(document.getElementById('batch-pause').value) || 300,
    };
    await API.saveMessage(message);
    config.message = message;
    renderSummary();
    const btn = document.getElementById('btn-save-msg');
    btn.textContent = '✓ Сохранено';
    setTimeout(() => btn.textContent = '💾 Сохранить', 1500);
};

document.getElementById('btn-send').onclick = async () => {
    const res = await API.send();
    if (!res.ok) return alert(res.error || 'Ошибка');
    startPolling();
};

document.getElementById('btn-stop').onclick = async () => {
    if (!confirm('Остановить отправку?')) return;
    await API.stop();
};

function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(updateProgress, 1000);
    updateProgress();
}

async function updateProgress() {
    const s = await API.status();
    const badge       = document.getElementById('status-badge');
    const progressCard = document.getElementById('progress-card');
    const resultsCard  = document.getElementById('results-card');
    const btnSend      = document.getElementById('btn-send');
    const btnStop      = document.getElementById('btn-stop');

    if (s.running || s.results.length > 0) progressCard.style.display = 'block';
    if (s.results.length > 0) resultsCard.style.display = 'block';

    if (s.running) {
        badge.textContent = `Отправка ${s.current}/${s.total}`;
        badge.classList.remove('hidden');
        btnSend.classList.add('hidden');
        btnStop.classList.remove('hidden');
    } else {
        if (s.finished && s.finished !== lastFinished) {
            lastFinished = s.finished;
            badge.textContent = `Готово: ✓${s.success} ✗${s.failed}`;
            badge.classList.remove('hidden');
            setTimeout(() => badge.classList.add('hidden'), 5000);
        }
        btnSend.classList.remove('hidden');
        btnStop.classList.add('hidden');
        if (pollTimer && s.finished) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    const pct = s.total > 0 ? (s.current / s.total * 100) : 0;
    document.getElementById('progress-fill').style.width = pct + '%';
    document.getElementById('progress-text').textContent =
        `${s.current} / ${s.total} · ✓ ${s.success} · ✗ ${s.failed}`;

    let curr = '';
    if (s.current_email) {
        curr = `Сейчас: ${s.current_email}`;
        if (s.next_at) {
            const wait = Math.max(0, s.next_at - Date.now() / 1000);
            curr += ` · след. через ${Math.ceil(wait)} сек`;
        }
    }
    document.getElementById('current-email').textContent = curr;

    const list = document.getElementById('results-list');
    list.innerHTML = '';
    s.results.forEach(r => {
        const div = document.createElement('div');
        div.className = 'result-item ' + (r.ok ? 'ok' : 'fail');
        div.innerHTML = `
            <span class="icon">${r.ok ? '✓' : '✗'}</span>
            <div class="text">
                <b>${r.email}</b>
                ${r.ok ? '' : `<span class="reason">${r.reason}</span>`}
            </div>
        `;
        list.appendChild(div);
    });
}

document.querySelectorAll('.bottomnav button').forEach(btn => {
    btn.onclick = () => {
        document.querySelectorAll('.bottomnav button').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
        window.scrollTo(0, 0);
    };
});

// PWA
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
}

// Init
loadConfig().then(() => updateProgress());