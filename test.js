
const BASE = '';
let adminToken = '';
let allClinics = [];

function toast(msg, type='success') {
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

// JWTトークン・Cookie認証用
function getCookie(name) {
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(';').shift();
  return '';
}

async function api(path, opts={}) {
  const headers = { 'Content-Type': 'application/json' };
  const csrf = getCookie('csrf_token');
  if (csrf) headers['X-CSRF-Token'] = csrf;

  const res = await fetch(`/api${path}`, {
    credentials: 'include',
    headers: headers,
    ...opts
  });
  if (res.status === 204) return {};
  const data = await res.json().catch(()=>({}));
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      // 権限エラー → ログアウト
      doLogout();
      throw new Error('セッションが切れました。再ログインしてください。');
    }
    throw new Error(data.detail || 'APIエラー');
  }
  return data;
}

async function doLogin() {
  const email = document.getElementById('adminEmail').value.trim();
  const pass  = document.getElementById('adminPassField').value;
  const btn   = document.getElementById('loginBtn');
  btn.disabled = true; btn.textContent = 'ログイン中...';
  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      credentials: 'include',
      body: JSON.stringify({ email, password: pass })
    });
    const data = await res.json();
    if (!res.ok) {
      document.getElementById('loginError').textContent = data.detail || 'ログインに失敗しました';
      document.getElementById('loginError').style.display = 'block';
    } else if (data.role !== 'admin') {
      document.getElementById('loginError').textContent = '管理者アカウントでログインしてください';
      document.getElementById('loginError').style.display = 'block';
    } else {
      localStorage.setItem('admu_user', JSON.stringify({ email: data.email, role: data.role, clinic_id: data.clinic_id }));
      document.getElementById('loginScreen').style.display = 'none';
      document.getElementById('adminApp').style.display = 'block';
      document.getElementById('loginError').style.display = 'none';
      initAdmin();
    }
  } catch(e) {
    document.getElementById('loginError').textContent = 'ネットワークエラー: ' + e.message;
    document.getElementById('loginError').style.display = 'block';
  }
  btn.disabled = false; btn.textContent = 'ログイン';
}

function doLogout() {
  localStorage.removeItem('admu_user');
  document.getElementById('adminApp').style.display = 'none';
  document.getElementById('loginScreen').style.display = 'flex';
  document.getElementById('adminPassField').value = '';
  // サーバー側のログアウトAPIを呼ぶ
  fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }).catch(()=>{});
}

// 自動ログイン（Cookie + 情報が保存済みの場合）
document.addEventListener('DOMContentLoaded', () => {
  const user  = JSON.parse(localStorage.getItem('admu_user') || '{}');
  if (user.role === 'admin') {
    fetch('/api/auth/me', { credentials: 'include' })
      .then(r => r.json())
      .then(d => {
        if (d.role === 'admin') {
          document.getElementById('loginScreen').style.display = 'none';
          document.getElementById('adminApp').style.display = 'block';
          initAdmin();
        } else { doLogout(); }
      })
      .catch(() => {
        // オフラインまたはCookie無効
        doLogout();
      });
  }
});

function switchTab(name) {
  document.querySelectorAll('.section-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.admin-nav button').forEach(b => b.classList.remove('active'));
  document.getElementById(`tab-${name}`).classList.add('active');
  event.target.classList.add('active');
  if (name === 'overview') loadOverview();
  if (name === 'contracts') loadContracts();
  if (name === 'clinics') loadClinicsManage();
  if (name === 'users') loadUsers();
  if (name === 'plan') loadPlanStatus();
}

/* ===== ユーザー管理 ===== */
let userModal = null;

function openUserModal() {
  const html = `
    <div class="modal-bg open" id="userModal" onclick="if(event.target===this)this.remove()">
      <div class="modal">
        <h3>👥 新規ユーザー発行</h3>
        <div class="form-group">
          <label>クリニック</label>
          <select id="newUserClinic" class="form-input">
            ${allClinics.map(c => `<option value="${c.clinic_id}">${c.clinic_name}</option>`).join('')}
          </select>
        </div>
        <div class="form-group">
          <label>メールアドレス</label>
          <input type="email" id="newUserEmail" class="form-input" placeholder="client@example.com">
        </div>
        <div class="form-group">
          <label>パスワード（8文字以上）</label>
          <input type="password" id="newUserPass" class="form-input" placeholder="強力なパスワードを設定">
        </div>
        <div class="form-group">
          <label>ロール</label>
          <select id="newUserRole" class="form-input">
            <option value="user">一般ユーザー</option>
            <option value="admin">管理者</option>
          </select>
        </div>
        <div class="modal-btns">
          <button class="btn btn-primary" onclick="saveUser()">発行する</button>
          <button class="btn btn-ghost" onclick="document.getElementById('userModal').remove()">キャンセル</button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
}

async function saveUser() {
  const body = {
    clinic_id: parseInt(document.getElementById('newUserClinic').value),
    email: document.getElementById('newUserEmail').value.trim(),
    password: document.getElementById('newUserPass').value,
    role: document.getElementById('newUserRole').value,
  };
  try {
    await api('/admin/users/create', { method: 'POST', body: JSON.stringify(body) });
    toast(`ユーザー ${body.email} を発行しました ✅`, 'success');
    document.getElementById('userModal')?.remove();
    loadUsers();
  } catch(e) { toast('発行失敗: '+e.message, 'error'); }
}

async function deleteUser(userId, email) {
  if (!confirm(`${email} を無効化しますか？`)) return;
  try {
    await api(`/admin/users/${userId}`, { method: 'DELETE' });
    toast('ユーザーを無効化しました', 'success');
    loadUsers();
  } catch(e) { toast('失敗: '+e.message, 'error'); }
}

async function loadUsers() {
  try {
    const data = await api('/admin/users');
    const users = data.users || [];
    document.getElementById('usersTable').innerHTML = `
      <table>
        <thead><tr>
          <th>メール</th><th>クリニック</th><th>ロール</th>
          <th>最終ログイン</th><th>ステータス</th><th>操作</th>
        </tr></thead>
        <tbody>
          ${users.map(u => `<tr>
            <td><strong>${u.email}</strong></td>
            <td>${u.clinic_name || '#'+u.clinic_id}</td>
            <td><span class="badge ${u.role === 'admin' ? 'badge-warning' : 'badge-active'}">${u.role === 'admin' ? '管理者' : '一般'}</span></td>
            <td style="font-size:11px;color:var(--text-3)">${u.last_login_at || '未ログイン'}</td>
            <td>${u.is_active ? '<span class="badge badge-active">有効</span>' : '<span class="badge badge-inactive">無効</span>'}</td>
            <td>
              ${u.is_active ? `<button class="btn btn-danger btn-sm" onclick="deleteUser(${u.id},'${u.email}')">無効化</button>` : ''}
            </td>
          </tr>`).join('')}
          ${users.length === 0 ? '<tr><td colspan="6" style="text-align:center;color:var(--text-3);padding:24px">ユーザーがいません。新規発行してください。</td></tr>' : ''}
        </tbody>
      </table>`;
  } catch(e) { toast('ユーザー取得失敗: '+e.message, 'error'); }
}

/* ===== プラン管理 ===== */
async function loadPlanStatus() {
  try {
    const data = await api('/admin/overview');
    const clinics = data.clinics || [];
    allClinics = clinics;
    document.getElementById('planStatusTable').innerHTML = `
      <table>
        <thead><tr>
          <th>クリニック</th><th>現在のプランステータス</th><th>変更</th>
        </tr></thead>
        <tbody>
          ${clinics.map(c => `<tr>
            <td><strong>${c.clinic_name}</strong></td>
            <td>
              <select id="plan_${c.clinic_id}" class="form-input" style="width:160px">
                <option value="active" ${c.plan_status==='active'||!c.plan_status?'selected':''}>✅ active（稼働中）</option>
                <option value="suspended" ${c.plan_status==='suspended'?'selected':''}>⏸ suspended（停止中）</option>
                <option value="cancelled" ${c.plan_status==='cancelled'?'selected':''}>❌ cancelled（解約）</option>
              </select>
            </td>
            <td>
              <button class="btn btn-primary btn-sm" onclick="updatePlanStatus(${c.clinic_id})">更新</button>
            </td>
          </tr>`).join('')}
        </tbody>
      </table>`;
  } catch(e) { toast('プラン状態取得失敗: '+e.message, 'error'); }
}

async function updatePlanStatus(clinicId) {
  const status = document.getElementById(`plan_${clinicId}`).value;
  try {
    await api('/admin/plan-status', {
      method: 'POST',
      body: JSON.stringify({ clinic_id: clinicId, status })
    });
    toast(`プランステータスを「${status}」に更新しました ✅`, 'success');
  } catch(e) { toast('更新失敗: '+e.message, 'error'); }
}

async function changeAdminPassword() {
  const oldPass    = document.getElementById('oldAdminPass').value;
  const newPass    = document.getElementById('newAdminPass').value;
  const confirmPass = document.getElementById('confirmAdminPass').value;
  if (!oldPass || !newPass) { toast('すべての項目を入力してください', 'error'); return; }
  if (newPass !== confirmPass) { toast('新しいパスワードが一致しません', 'error'); return; }
  if (newPass.length < 4) { toast('パスワードは4文字以上にしてください', 'error'); return; }
  if (oldPass !== '' && oldPass.length < 4) { toast('パスワードが短すぎます', 'error'); return; }
  try {
    const res = await fetch('/api/admin/change-password', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      credentials: 'include',
      body: JSON.stringify({ old_password: oldPass, new_password: newPass })
    });
    const data = await res.json();
    if (data.success) {
      toast('パスワードを変更しました ✅ 次回ログインから有効です', 'success');
      document.getElementById('oldAdminPass').value = '';
      document.getElementById('newAdminPass').value = '';
      document.getElementById('confirmAdminPass').value = '';
    } else {
      toast(data.detail || '変更に失敗しました', 'error');
    }
  } catch(e) { toast('エラー: ' + e.message, 'error'); }
}

async function initAdmin() {
  await loadOverview();
  await loadContracts();
  await populateClinicSelects();
  await loadUsers();
}

async function loadOverview() {
  try {
    const data = await api('/admin/overview');
    const clinics = data.clinics || [];
    allClinics = clinics;
    document.getElementById('adminClinicCount').textContent = `契約クリニック: ${clinics.length}院`;

    const totalMRR = clinics.reduce((s, c) => s + 0, 0);
    const active = clinics.filter(c => c.status === 'active').length;

    // Check renewal alerts (within 30 days)
    const now = new Date();
    const alertCount = clinics.filter(c => {
      if (!c.renewal_at) return false;
      const diff = (new Date(c.renewal_at) - now) / (1000 * 60 * 60 * 24);
      return diff <= 30 && diff >= 0;
    }).length;

    document.getElementById('overviewKPIs').innerHTML = `
      <div class="kpi-box"><div class="kpi-label">総クリニック数</div><div class="kpi-value">${clinics.length}</div></div>
      <div class="kpi-box"><div class="kpi-label">稼働中</div><div class="kpi-value" style="color:var(--green)">${active}</div></div>
      <div class="kpi-box" style="border-color:${alertCount>0?'var(--warning)':''}">
        <div class="kpi-label">更新期限アラート（30日内）</div>
        <div class="kpi-value" style="color:var(--warning)">${alertCount}</div>
      </div>
      <div class="kpi-box"><div class="kpi-label">未契約</div><div class="kpi-value" style="color:var(--text-3)">${clinics.filter(c=>c.status==='inactive'||!c.plan_name||c.plan_name==='未契約').length}</div></div>
    `;

    document.getElementById('overviewTable').innerHTML = `
      <table>
        <thead><tr>
          <th>クリニック</th><th>プラン</th><th>次回更新日</th>
          <th>7日間費用</th><th>7日間CV</th><th>ステータス</th>
        </tr></thead>
        <tbody>
          ${clinics.map(c => {
            const cost = Math.round((c.total_cost_micros||0)/1e6);
            const renewal = c.renewal_at ? new Date(c.renewal_at) : null;
            const daysLeft = renewal ? Math.ceil((renewal - now) / 86400000) : null;
            const renewalText = renewal
              ? `${c.renewal_at} ${daysLeft <= 30 && daysLeft >= 0 ? `<span class="renewal-alert">⚠ 残${daysLeft}日</span>` : ''}`
              : '未設定';
            const statusBadge = {
              active: '<span class="badge badge-active">稼働中</span>',
              trial: '<span class="badge badge-warning">トライアル</span>',
              paused: '<span class="badge badge-inactive">停止中</span>',
              cancelled: '<span class="badge badge-inactive">解約済</span>',
              inactive: '<span class="badge" style="background:#334155;color:var(--text-3)">未契約</span>',
            }[c.status || 'inactive'] || '';
            return `<tr>
              <td><strong>${c.clinic_name}</strong></td>
              <td>${c.plan_name || '未契約'}</td>
              <td>${renewalText}</td>
              <td>¥${cost.toLocaleString()}</td>
              <td>${c.total_conversions || 0}</td>
              <td>${statusBadge}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    `;
  } catch(e) { toast('概要取得失敗: '+e.message, 'error'); }
}

async function loadContracts() {
  try {
    const data = await api('/admin/contracts');
    const contracts = data.contracts || [];
    document.getElementById('contractsTable').innerHTML = `
      <table>
        <thead><tr>
          <th>クリニック</th><th>プラン</th><th>月額</th>
          <th>契約開始</th><th>次回更新</th><th>ステータス</th><th>操作</th>
        </tr></thead>
        <tbody>
          ${contracts.map(c => {
            const statusBadge = {
              active: '<span class="badge badge-active">稼働中</span>',
              trial: '<span class="badge badge-warning">トライアル</span>',
              paused: '<span class="badge badge-inactive">停止中</span>',
              cancelled: '<span class="badge badge-inactive">解約済</span>',
            }[c.status || 'inactive'] || '<span class="badge" style="background:#334155;color:var(--text-3)">未契約</span>';
            return `<tr>
              <td><strong>${c.clinic_name}</strong></td>
              <td>${c.plan_name || '未設定'}</td>
              <td>${c.monthly_fee ? '¥'+Number(c.monthly_fee).toLocaleString() : '未設定'}</td>
              <td>${c.started_at || '-'}</td>
              <td>${c.renewal_at || '-'}</td>
              <td>${statusBadge}</td>
              <td>
                <button class="btn btn-ghost btn-sm" onclick="openContractModal(${c.clinic_id})">編集</button>
              </td>
            </tr>`;
          }).join('')}
          ${contracts.length === 0 ? '<tr><td colspan="7" style="text-align:center;color:var(--text-3);padding:24px">まだ契約情報がありません</td></tr>' : ''}
        </tbody>
      </table>
    `;
  } catch(e) { toast('契約取得失敗: '+e.message, 'error'); }
}

async function populateClinicSelects() {
  try {
    const data = await api('/admin/overview');
    const clinics = data.clinics || [];
    allClinics = clinics;

    const contractSel = document.getElementById('contractClinicId');
    const viewerSel = document.getElementById('viewerClinicSelect');
    const opts = clinics.map(c => `<option value="${c.clinic_id}">${c.clinic_name}</option>`).join('');
    contractSel.innerHTML = opts;
    viewerSel.innerHTML = '<option value="">クリニックを選択...</option>' + opts;
  } catch(e) {}
}

function openContractModal(clinicId) {
  document.getElementById('contractModal').classList.add('open');
  if (clinicId) {
    document.getElementById('contractClinicId').value = clinicId;
  }
}

async function saveContract() {
  const data = {
    clinic_id: parseInt(document.getElementById('contractClinicId').value),
    plan_name: document.getElementById('contractPlan').value,
    monthly_fee: parseInt(document.getElementById('contractFee').value||0),
    started_at: document.getElementById('contractStart').value||null,
    renewal_at: document.getElementById('contractRenewal').value||null,
    status: document.getElementById('contractStatus').value,
    notes: document.getElementById('contractNotes').value||null,
  };
  try {
    const sep = '?';
    await fetch(`/api/admin/contracts`, {
      method: 'POST', headers: {'Content-Type':'application/json'}, 
      credentials: 'include',
      body: JSON.stringify(data)
    });
    toast('契約情報を保存しました ✅', 'success');
    closeModal('contractModal');
    loadContracts(); loadOverview();
  } catch(e) { toast('保存失敗: '+e.message, 'error'); }
}

async function loadClinicData() {
  const clinicId = document.getElementById('viewerClinicSelect').value;
  if (!clinicId) { toast('クリニックを選択してください', 'error'); return; }
  try {
    const data = await api(`/admin/clinics/${clinicId}/data`);
    const clinicName = data.clinic?.name || `クリニックID:${clinicId}`;
    renderClinicData(data, clinicId, clinicName);
  } catch(e) { toast('データ取得失敗: '+e.message, 'error'); }
}

function renderClinicData(data, clinicId, clinicName) {
  const campaigns = data.campaigns || [];
  const negKws = data.negative_keywords || [];
  const adCopies = data.ad_copies || [];
  const alerts = data.alerts || [];
  document.getElementById('clinicDataView').innerHTML = `
    <div class="card">
      <div class="card-title">📂 ${clinicName} の広告データ</div>

      <div class="data-section">
        <h4>🚀 キャンペーン（${campaigns.length}件）</h4>
        ${campaigns.map(c => `
          <div class="data-item">
            <div>
              <div style="font-weight:600">${c.name}</div>
              <div style="font-size:11px;color:var(--text-3)">ステータス: ${c.status} | ID: ${c.id}</div>
            </div>
          </div>
        `).join('') || '<p style="color:var(--text-3);font-size:13px">データなし</p>'}
      </div>

      <div class="data-section">
        <h4>🚫 除外キーワード（${negKws.length}件）</h4>
        <div style="display:flex;flex-wrap:wrap;gap:6px">
          ${negKws.slice(0,20).map(k => `
            <span style="display:inline-flex;align-items:center;gap:6px;background:#1e293b;border:1px solid var(--border);border-radius:6px;padding:3px 10px;font-size:12px">
              ${k.keyword}
              ${!k.applied ? `<button class="btn btn-sm btn-green" style="padding:2px 6px;font-size:10px" onclick="applyData(${clinicId},'apply_negative_kw',${k.id})">反映</button>` : '<span style="color:var(--green);font-size:10px">✅適用済</span>'}
            </span>
          `).join('') || '<span style="color:var(--text-3);font-size:13px">なし</span>'}
        </div>
      </div>

      <div class="data-section">
        <h4>✍️ AI広告文（${adCopies.length}件）</h4>
        ${adCopies.slice(0,5).map(a => {
          let headlines = [];
          try { headlines = JSON.parse(a.headlines||'[]'); } catch(e){}
          return `
          <div class="data-item">
            <div style="flex:1">
              <div style="font-size:12px;color:var(--text-2)">ステータス: ${a.status}</div>
              <div style="font-size:13px;margin-top:2px">${headlines.slice(0,2).join(' | ')}</div>
            </div>
            ${a.status !== 'active' ? `<button class="btn btn-sm btn-green" onclick="applyData(${clinicId},'apply_ad_copy',${a.id})">有効化</button>` : '<span class="badge badge-active">有効</span>'}
          </div>`;
        }).join('') || '<p style="color:var(--text-3);font-size:13px">なし</p>'}
      </div>

      <div class="data-section">
        <h4>🔔 アラート（直近${Math.min(alerts.length,5)}件）</h4>
        ${alerts.slice(0,5).map(a => `
          <div class="data-item">
            <div style="font-size:13px">${a.message}</div>
            <div style="font-size:11px;color:var(--text-3)">${a.created_at||''}</div>
          </div>
        `).join('') || '<p style="color:var(--text-3);font-size:13px">なし</p>'}
      </div>
    </div>
  `;
}

async function applyData(clinicId, action, targetId) {
  if (!confirm('この操作を広告運用システムに反映しますか？')) return;
  try {
    const res = await fetch(`/api/admin/apply`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      credentials: 'include',
      body: JSON.stringify({ clinic_id: clinicId, action, target_id: targetId })
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    toast(data.message || '反映しました ✅', 'success');
    loadClinicData();
  } catch(e) { toast('反映失敗: '+e.message, 'error'); }
}

async function loadClinicsManage() {
  try {
    const data = await api('/admin/overview');
    const clinics = data.clinics || [];
    document.getElementById('clinicsManageTable').innerHTML = `
      <table>
        <thead><tr><th>ID</th><th>クリニック名</th><th>プラン</th><th>ステータス</th><th>操作</th></tr></thead>
        <tbody>
          ${clinics.map(c => `<tr>
            <td style="color:var(--text-3)">#${c.clinic_id}</td>
            <td><strong>${c.clinic_name}</strong></td>
            <td>${c.plan_name||'未契約'}</td>
            <td>${c.status === 'active' ? '<span class="badge badge-active">稼働中</span>' : '<span class="badge" style="background:#334155;color:var(--text-3)">未契約</span>'}</td>
            <td>
              <button class="btn btn-ghost btn-sm" onclick="openClinicModal(${c.clinic_id},'${c.clinic_name}')">編集</button>
            </td>
          </tr>`).join('')}
        </tbody>
      </table>
    `;
  } catch(e) { toast('取得失敗', 'error'); }
}

function openClinicModal(id, name) {
  document.getElementById('clinicEditId').value = id || '';
  document.getElementById('clinicName').value = name || '';
  document.getElementById('clinicLicenseKey').value = '';
  document.getElementById('clinicModal').classList.add('open');
}

async function saveClinic() {
  const name = document.getElementById('clinicName').value.trim();
  if (!name) { toast('クリニック名は必須です', 'error'); return; }
  const id = document.getElementById('clinicEditId').value;
  const body = {
    id: id ? parseInt(id) : null,
    name,
    license_key: document.getElementById('clinicLicenseKey').value.trim() || null
  };
  try {
    await fetch('/api/admin/clinics', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      credentials: 'include',
      body: JSON.stringify(body)
    });
    toast('保存しました ✅', 'success');
    closeModal('clinicModal');
    loadClinicsManage(); populateClinicSelects();
  } catch(e) { toast('保存失敗: '+e.message, 'error'); }
}

function closeModal(id) {
  document.getElementById(id).classList.remove('open');
}

// Close modal on background click
document.querySelectorAll('.modal-bg').forEach(m => {
  m.addEventListener('click', e => { if(e.target === m) m.classList.remove('open'); });
});

// Sentry initialization
document.addEventListener('DOMContentLoaded', () => {
  fetch('/api/config', { credentials: 'omit' })
    .then(res => res.json())
    .then(config => {
      if (config.sentry_dsn && typeof Sentry !== 'undefined') {
        Sentry.init({
          dsn: config.sentry_dsn,
          integrations: [new Sentry.BrowserTracing()],
          tracesSampleRate: 1.0,
        });
        console.log('[Sentry] Admin panel init completed');
      }
    })
    .catch(err => console.warn('Config load failed:', err));
});
