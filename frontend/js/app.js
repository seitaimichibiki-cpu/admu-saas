/* ==============================================================
   app.js - Google広告自動運用システム メインJS
   ============================================================== */

const API = 'http://localhost:8001/api';

// ============================================================
// 認証管理 (JWT)
// ============================================================
const AUTH_KEY = 'admu_token';
const USER_KEY = 'admu_user';

function getToken() { return localStorage.getItem(AUTH_KEY); }
function getUser()  { try { return JSON.parse(localStorage.getItem(USER_KEY) || '{}'); } catch { return {}; } }

function authHeaders() {
  const t = getToken();
  return t ? { 'Authorization': `Bearer ${t}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' };
}

// ログイン画面を表示
function showLoginScreen() {
  document.getElementById('loginScreen').classList.add('active');
  document.getElementById('loginBtn').disabled = false;
  document.getElementById('loginEmail').value = '';
  document.getElementById('loginPassword').value = '';
  document.getElementById('loginError').classList.remove('show');
}

// ダッシュボードを表示（ログイン後）
function showDashboard(user) {
  document.getElementById('loginScreen').classList.remove('active');
  const emailEl = document.getElementById('loggedInEmail');
  const userEl  = document.getElementById('loggedInUser');
  const logoutEl = document.getElementById('logoutBtn');
  if (emailEl) emailEl.textContent = user.email || '';
  if (userEl)  userEl.style.display = 'block';
  if (logoutEl) logoutEl.style.display = 'block';
  // adminの場合は管理者ナビを表示
  const navAdmin = document.getElementById('navAdmin');
  if (navAdmin) navAdmin.style.display = (user.role === 'admin') ? 'flex' : 'none';

  // オンボーディングチェック
  if (!localStorage.getItem(`admu_onboarding_done_${user.email}`)) {
    setTimeout(() => startOnboarding(user.email), 800);
  }

  // お知らせ読み込み
  loadAnnouncements();
}

// ログイン実行
window.doLogin = async function doLogin() {
  const email    = document.getElementById('loginEmail').value.trim();
  const password = document.getElementById('loginPassword').value;
  const errEl    = document.getElementById('loginError');
  const btn      = document.getElementById('loginBtn');
  if (!email || !password) {
    errEl.textContent = 'メールアドレスとパスワードを入力してください。';
    errEl.classList.add('show');
    return;
  }
  btn.disabled = true;
  btn.textContent = 'ログイン中...';
  errEl.classList.remove('show');
  try {
    const res = await fetch(`${API}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });
    const data = await res.json();
    if (!res.ok) {
      errEl.textContent = data.detail || 'ログインに失敗しました。';
      errEl.classList.add('show');
      btn.disabled = false; btn.textContent = 'ログイン';
      return;
    }
    // 成功
    localStorage.setItem(AUTH_KEY, data.access_token);
    localStorage.setItem(USER_KEY, JSON.stringify({
      email: data.email,
      role: data.role,
      clinic_id: data.clinic_id,
      plan_type:     data.plan_type     || 'standard',
      plan_name:     data.plan_name     || 'スタンダード',
      yahoo_enabled: data.yahoo_enabled !== false,
    }));
    if (data.clinic_id) currentClinicId = data.clinic_id;
    showDashboard(data);
    btn.textContent = 'ログイン';
    loadDashboard();
    loadClinics();
  } catch(e) {
    errEl.textContent = 'ネットワークエラーが発生しました。サーバーに接続できません。';
    errEl.classList.add('show');
    btn.disabled = false; btn.textContent = 'ログイン';
  }
};

// ログアウト
window.doLogout = function doLogout() {
  if (!confirm('ログアウトしますか？')) return;
  localStorage.removeItem(AUTH_KEY);
  localStorage.removeItem(USER_KEY);
  const userEl  = document.getElementById('loggedInUser');
  const logoutEl = document.getElementById('logoutBtn');
  if (userEl)   userEl.style.display = 'none';
  if (logoutEl) logoutEl.style.display = 'none';
  showLoginScreen();
};

// Enterキーでログイン
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('loginPassword')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') doLogin();
  });
  document.getElementById('loginEmail')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('loginPassword')?.focus();
  });

  // パスワードリセット処理のフック
  const params = new URLSearchParams(window.location.search);
  const resetToken = params.get('reset_token');
  if(resetToken) {
    document.getElementById('resetConfirmToken').value = resetToken;
    document.getElementById('publicResetConfirmModalOverlay').style.display = 'flex';
    window.history.replaceState({}, document.title, window.location.pathname);
    return;
  }

  // JWTが保存済みなら自動的にダッシュボードを表示
  const token = getToken();
  const user  = getUser();
  if (token && user.email) {
    // サーバーで検証
    fetch(`${API}/auth/me`, { headers: authHeaders() })
      .then(r => {
        if (r.ok) {
          showDashboard(user);
          if (user.clinic_id) currentClinicId = user.clinic_id;
        } else {
          // トークン無効 → ログアウト
          localStorage.removeItem(AUTH_KEY);
          localStorage.removeItem(USER_KEY);
          showLoginScreen();
        }
      })
      .catch(() => {
        // オフライン時はトークンがあればそのまま表示（開発用フォールバック）
        showDashboard(user);
      });
  } else {
    // ── localhost限定: 自動ログイン ──────────────────────────
    if (location.hostname === 'localhost' || location.hostname === '127.0.0.1') {
      // localhostなら自動でadminとしてログイン（操作不要）
      fetch(`${API}/auth/dev-autologin`, { method: 'POST' })
        .then(r => r.json())
        .then(data => {
          if (data.access_token) {
            localStorage.setItem(AUTH_KEY, data.access_token);
            localStorage.setItem(USER_KEY, JSON.stringify({
              email:         data.email,
              role:          data.role,
              clinic_id:     data.clinic_id,
              plan_type:     data.plan_type     || 'standard',
              plan_name:     data.plan_name     || 'スタンダード',
              yahoo_enabled: data.yahoo_enabled !== false,
            }));
            if (data.clinic_id) currentClinicId = data.clinic_id;
            applyPlanRestrictions(data);
            showDashboard(data);
            loadDashboard();
            loadClinics();
          } else {
            showLoginScreen();
          }
        })
        .catch(() => showLoginScreen());
    } else {
      showLoginScreen();
    }
  }
});

// ── プラットフォーム管理 ──────────────────────────
let currentPlatform = localStorage.getItem('admu_platform') || 'google';

window.switchPlatform = function switchPlatform(platform) {
  // ── STARTERplanはYahooをブロック ────────────────────────────────
  if (platform === 'yahoo') {
    const user = getUser();
    if (user.yahoo_enabled === false || user.plan_type === 'starter') {
      toast('🔒 Yahoo!広告はSTANDARDプラン以上で利用できます。プランのアップグレードを詳はサポートまでご連絡ください。', 'error', 5000);
      return; // 処理中断―Yahooに切り替えない
    }
  }

  currentPlatform = platform;
  localStorage.setItem('admu_platform', platform);

  // ボタンスタイル更新
  document.getElementById('btnGoogle').className = 'platform-btn' + (platform === 'google' ? ' active-google' : '');
  document.getElementById('btnYahoo').className  = 'platform-btn' + (platform === 'yahoo'  ? ' active-yahoo'  : '');

  // バッジ更新
  const badge = document.getElementById('platformBadge');
  if(badge) {
    if(platform === 'google') {
      badge.textContent = '🔵 Google広告';
      badge.className = 'badge-google';
      badge.style.display = 'inline-flex';
    } else {
      badge.textContent = '🔴 Yahoo!広告';
      badge.className = 'badge-yahoo';
      badge.style.display = 'inline-flex';
    }
  }

  // モックモードバッジのテキストも更新
  const mockBadge = document.getElementById('mockBadge');
  if(mockBadge) {
    mockBadge.textContent = platform === 'google' ? '● モックモード（Google）' : '● モックモード（Yahoo!）';
  }

  // データ再読み込み
  toast(platform === 'google' ? 'Google広告に切り替えました' : 'Yahoo!広告に切り替えました', 'success', 2000);
  loadDashboard();
  if(typeof loadCampaigns === 'function') loadCampaigns();
}

let currentClinicId = 1;
let currentDaysRange = '7';   // '7'/'14'/'30'/'this_year'/'last_year'/'custom'
let dashCustomStart = '';
let dashCustomEnd = '';
let monthlyBudgetYen = 300000; // 月予算（設定画面から変更可）
let perfChart = null;
let costChart = null;
let lastData = null;

// 日付範囲切り替え
window.setDateRange = function(days) {
  currentDaysRange = String(days);
  // ボタンの見た目を更新
  ['7', '14', '30', 'this_year', 'last_year', 'custom'].forEach(d => {
    let suffix = d === 'this_year' ? 'ThisYear' : (d === 'last_year' ? 'LastYear' : (d === 'custom' ? 'Custom' : d));
    const btn = document.getElementById(`rangeBtn${suffix}`);
    if (btn) btn.classList.toggle('range-active', d === currentDaysRange);
  });

  const customWrap = document.getElementById('dashCustomRangeWrap');
  if (customWrap) customWrap.style.display = currentDaysRange === 'custom' ? 'flex' : 'none';

  if (currentDaysRange !== 'custom') {
    let label = `${currentDaysRange}日間`;
    if(currentDaysRange === 'this_year') label = '今年';
    if(currentDaysRange === 'last_year') label = '昨年';
    const chartTitle = document.querySelector('#page-dashboard .chart-header h3');
    if (chartTitle) chartTitle.textContent = `${label}パフォーマンス`;
    loadDashboard();
  }
};

window.applyDashCustomDate = function() {
  const start = document.getElementById('dashCustomStart').value;
  const end = document.getElementById('dashCustomEnd').value;
  if(!start || !end) {
    toast('開始日と終了日を選択してください', 'error');
    return;
  }
  dashCustomStart = start;
  dashCustomEnd = end;
  const chartTitle = document.querySelector('#page-dashboard .chart-header h3');
  if (chartTitle) chartTitle.textContent = `指定期間パフォーマンス`;
  loadDashboard();
};

// ============================================================
// オンボーディングチェック（初回利用時にウィザードへ誘導）
// ============================================================
let onboardingStep = 0;
let onboardingUserEmail = '';
const onboardingSteps = [
  { title: "👋 AdMuへようこそ！", content: "AI自動運用システム「AdMu」にようこそ。まずはダッシュボードで現状の広告費と獲得状況を確認しましょう。左半分のグラフが直近の状況です。" },
  { title: "💰 予算の設定", content: "メインメニューの「設定」画面から、月間予算と目標CPA（獲得単価）を設定するだけ。あとはAIが自動で入札価格をコントロールします。" },
  { title: "🤖 AI広告文生成", content: "「AI広告文生成」メニューでは、あなたの整体院の強みをもとに勝てる広告文を自動で作ってくれます。さあ、自動運用をスタートしましょう！" }
];

window.startOnboarding = function(email) {
  onboardingStep = 0;
  onboardingUserEmail = email;
  renderOnboardingModal();
};

window.renderOnboardingModal = function() {
  const step = onboardingSteps[onboardingStep];
  if (!step) {
    // 完了
    localStorage.setItem(`admu_onboarding_done_${onboardingUserEmail}`, 'true');
    closeModal();
    return;
  }
  const footer = `
    <div style="display:flex;justify-content:space-between;width:100%;align-items:center">
      <div style="color:var(--text-3);font-size:12px">${onboardingStep + 1} / ${onboardingSteps.length}</div>
      <button class="btn btn-primary" onclick="onboardingStep++; renderOnboardingModal();">
        ${onboardingStep === onboardingSteps.length - 1 ? 'はじめる' : '次へ &rarr;'}
      </button>
    </div>
  `;
  showModal(step.title, `<p style="font-size:14px;color:var(--text-3);line-height:1.6">${step.content}</p>`, footer);
};


// ============================================================
// ユーティリティ
// ============================================================
function microsToYen(m) { return `¥${Math.round((m||0)/1e6).toLocaleString()}`; }
function microsToYenNum(m) { return Math.round((m||0)/1e6); }
function fmtNum(n) { return (n||0).toLocaleString(); }
function fmtPct(n) { return `${(n||0).toFixed(2)}%`; }
function fmtDate(s) { return s ? s.replace('T',' ').slice(0,16) : '-'; }

async function api(path, options={}) {
  try {
    const res = await fetch(API + path, {
      headers: authHeaders(),
      ...options,
    });
    if (!res.ok) {
      const err = await res.json().catch(()=>({}));
      throw new Error(err.detail || err.error || `HTTP ${res.status}`);
    }
    return await res.json();
  } catch(e) {
    throw e;
  }
}

function toast(msg, type='info', duration=3500) {
  const icons = { success:'✅', error:'❌', info:'ℹ️' };
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.innerHTML = `<span>${icons[type]||'📢'}</span><span>${msg}</span>`;
  document.getElementById('toastContainer').prepend(t);
  setTimeout(()=>t.remove(), duration);
}

function showModal(title, bodyHTML, footerHTML='') {
  document.getElementById('modalTitle').textContent = title;
  document.getElementById('modalBody').innerHTML = bodyHTML;
  document.getElementById('modalFooter').innerHTML = footerHTML;
  document.getElementById('modalOverlay').style.display = 'flex';
}
function closeModal() {
  document.getElementById('modalOverlay').style.display = 'none';
}
document.getElementById('modalClose').addEventListener('click', closeModal);
document.getElementById('modalOverlay').addEventListener('click', e => {
  if(e.target === document.getElementById('modalOverlay')) closeModal();
});

// ============================================================
// ナビゲーション
// ============================================================
const PAGE_TITLES = {
  dashboard: 'ダッシュボード',
  analyze: '過去データ解析',
  campaigns: 'キャンペーン管理',
  budget: '予算設定（手動）',
  'bid-rules': '入札ルール設定',
  'ad-copy': 'AI広告文生成',
  alerts: 'アラート・ログ',
  settings: '設定',
  admin: '管理者パネル',
  help: 'ヘルプ＆マニュアル',
  legal: '法務情報',
};

window.showLoginResetPassword = function() {
  const overlay = document.getElementById('publicResetModalOverlay');
  if(overlay) overlay.style.display = 'flex';
};

window.doRequestPasswordReset = async function() {
  const email = document.getElementById('resetReqEmail').value;
  if (!email) {
    toast('メールアドレスを入力してください', 'error');
    return;
  }
  try {
    const res = await fetch(`${API}/auth/reset-password-request`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email })
    }).then(r => r.json());
    if (res.success) {
      toast(res.message, 'success', 5000);
      document.getElementById('publicResetModalOverlay').style.display = 'none';
      document.getElementById('resetReqEmail').value = '';
    } else {
      toast(res.detail || 'エラーが発生しました', 'error');
    }
  } catch (e) {
    toast('通信エラー', 'error');
  }
};

window.doConfirmPasswordReset = async function() {
  const token = document.getElementById('resetConfirmToken').value;
  const new_password = document.getElementById('resetConfirmPassword').value;
  if (!new_password || new_password.length < 6) {
    toast('パスワードは6文字以上で入力してください', 'error');
    return;
  }
  try {
    const res = await fetch(`${API}/auth/reset-password-confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, new_password })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      toast(data.message, 'success', 5000);
      document.getElementById('publicResetConfirmModalOverlay').style.display = 'none';
      document.getElementById('resetConfirmPassword').value = '';
      showLoginScreen();
    } else {
      toast(data.detail || 'トークンが無効または有効期限切れです', 'error');
    }
  } catch (e) {
    toast('通信エラー', 'error');
  }
};

window.showLoginLegal = function() {
  const overlay = document.getElementById('publicLegalModalOverlay');
  if(overlay) overlay.style.display = 'flex';
};

function switchPage(page) {
  document.querySelectorAll('.page').forEach(el => el.classList.add('hidden'));
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  const target = document.getElementById(`page-${page}`);
  if(target) target.classList.remove('hidden');
  const navItem = document.querySelector(`[data-page="${page}"]`);
  if(navItem) navItem.classList.add('active');
  document.getElementById('pageTitle').textContent = PAGE_TITLES[page] || page;

  // ページ別データ読み込み
  const loaders = {
    dashboard: loadDashboard,
    campaigns: loadCampaigns,
    budget: loadBudget,
    'bid-rules': loadBidRules,
    'ad-copy': loadAdCopyHistory,
    'negative-kw': loadNegativeKeywords,
    personas: loadPersonas,
    'lp-diagnosis': loadLpDiag,
    'kw-suggest': loadKwSuggest,
    competitor: loadCompetitor,
    alerts: loadAlerts,
    settings: loadSettings,
    admin: loadAdminPage,
  };
  if(loaders[page]) loaders[page]();

  // モバイルではサイドバーを閉じる
  toggleSidebar(true);
}

document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', e => {
    e.preventDefault();
    switchPage(item.dataset.page);
  });
});

document.getElementById('mobileMenuBtn').addEventListener('click', () => {
  toggleSidebar();
});
document.getElementById('sidebarToggle').addEventListener('click', () => {
  toggleSidebar();
});

function toggleSidebar(forceClose = false) {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebarOverlay');
  const isOpen = sidebar.classList.contains('open');
  if (forceClose || isOpen) {
    sidebar.classList.remove('open');
    overlay.classList.remove('active');
  } else {
    sidebar.classList.add('open');
    overlay.classList.add('active');
  }
}

// オーバーレイをタップしてサイドバーを閉じる
document.getElementById('sidebarOverlay').addEventListener('click', () => {
  toggleSidebar(true);
});

document.getElementById('refreshBtn').addEventListener('click', () => {
  const active = document.querySelector('.nav-item.active');
  if(active) switchPage(active.dataset.page);
});

// ============================================================
// クリニック選択
// ============================================================
async function loadClinics() {
  try {
    const data = await api('/clinics');
    const sel = document.getElementById('clinicSelect');
    sel.innerHTML = data.clinics.map(c =>
      `<option value="${c.id}">${c.name}</option>`).join('');
    if(data.clinics.length > 0) {
      currentClinicId = data.clinics[0].id;
    }
  } catch(e) {
    console.warn('クリニック一覧取得失敗:', e);
  }
}
document.getElementById('clinicSelect').addEventListener('change', function() {
  currentClinicId = parseInt(this.value);
  switchPage(document.querySelector('.nav-item.active')?.dataset.page || 'dashboard');
});

// ============================================================
// ダッシュボード
// ============================================================
async function loadDashboard() {
  try {
    const platform = document.getElementById('btnYahoo')?.classList.contains('active-yahoo') ? 'yahoo' : 'google';
    const qs = (currentDaysRange === 'custom' && dashCustomStart && dashCustomEnd)
      ? `?clinic_id=${currentClinicId}&platform=${platform}&days=${currentDaysRange}&start_date=${dashCustomStart}&end_date=${dashCustomEnd}`
      : `?clinic_id=${currentClinicId}&platform=${platform}&days=${currentDaysRange}`;
    const data = await api(`/dashboard${qs}`);
    // 月予算をDB設定値から同期
    const acc = data.settings;
    if (acc && acc.monthly_budget_yen) monthlyBudgetYen = acc.monthly_budget_yen;
    lastData = data;
    renderKPIs(data.summary);
    renderCharts(data.performance_series);
    renderDashCampaigns(data.campaigns);
    updateMonitorStatus(data.monitor_status);
    updateMockBadge(data.mock_mode);
    document.getElementById('lastUpdated').textContent = '更新: ' + new Date().toLocaleTimeString('ja-JP');

    // アラートバッジ
    const badge = document.getElementById('alertBadge');
    const alertCount = (data.recent_alerts||[]).filter(a=>!a.notified).length;
    if(alertCount > 0) {
      badge.textContent = alertCount;
      badge.style.display = 'flex';
    } else {
      badge.style.display = 'none';
    }

    // 成果予測カードを読み込み
    loadForecast();
  } catch(e) {
    toast('ダッシュボードの読み込みに失敗しました: ' + e.message, 'error');
  }
}

function renderKPIs(summary) {
  let periodLabel = `${currentDaysRange}日`;
  if(currentDaysRange === 'this_year') periodLabel = '今年';
  if(currentDaysRange === 'last_year') periodLabel = '昨年';
  if(currentDaysRange === 'custom') periodLabel = '指定期間';
  
  const kpis = [
    { label: `総費用（${periodLabel}）`, value: microsToYen(summary.total_cost_micros), icon: '💰', color: '#3b82f6' },
    { label: 'クリック数', value: fmtNum(summary.total_clicks), icon: '🖱', color: '#10b981' },
    { label: '表示回数', value: fmtNum(summary.total_impressions), icon: '👁', color: '#8b5cf6' },
    { label: '平均CTR', value: fmtPct(summary.avg_ctr), icon: '📈', color: '#f59e0b' },
    { label: 'コンバージョン', value: (summary.total_conversions||0).toFixed(1), icon: '🎯', color: '#06b6d4' },
    { label: '配信中キャンペーン', value: summary.active_campaigns, icon: '🚀', color: '#10b981' },
  ];

  // 予算消化率ゲージ計算（設定画面の月予算変数を使用）
  const MONTHLY_BUDGET_YEN = monthlyBudgetYen;
  const DAILY_BUDGET_YEN = MONTHLY_BUDGET_YEN / 30;
  const PERIOD_BUDGET = DAILY_BUDGET_YEN * currentDaysRange;
  const spentYen = Math.round((summary.total_cost_micros || 0) / 1e6);
  const burnPct = Math.min(100, Math.round(spentYen / PERIOD_BUDGET * 100));
  const burnColor = burnPct >= 90 ? '#ef4444' : burnPct >= 70 ? '#f59e0b' : '#10b981';
  const burnLabel = burnPct >= 90 ? '⚠️ 消化過多' : burnPct >= 70 ? '▲ 注意' : '✅ 正常';

  document.getElementById('kpiGrid').innerHTML = kpis.map(k => `
    <div class="kpi-card" style="--kpi-color:${k.color}">
      <div class="kpi-icon" style="background:${k.color}22">${k.icon}</div>
      <div class="kpi-label">${k.label}</div>
      <div class="kpi-value">${k.value}</div>
    </div>
  `).join('') + `
    <div class="kpi-card" style="--kpi-color:${burnColor};grid-column:span 1">
      <div class="kpi-icon" style="background:${burnColor}22">📊</div>
      <div class="kpi-label">予算消化率（7日）<span style="font-size:10px;margin-left:6px;color:${burnColor}">${burnLabel}</span></div>
      <div class="kpi-value">${burnPct}%</div>
      <div style="margin-top:8px;background:rgba(255,255,255,0.08);border-radius:99px;height:6px;overflow:hidden">
        <div style="height:100%;width:${burnPct}%;background:${burnColor};border-radius:99px;transition:width 0.6s ease"></div>
      </div>
      <div style="font-size:10px;color:var(--text-3);margin-top:4px">¥${spentYen.toLocaleString()} / ¥${Math.round(PERIOD_BUDGET).toLocaleString()}</div>
    </div>
  `;
}

function renderCharts(series) {
  const labels = series.map(s => s.date.slice(5));
  const ctrs   = series.map(s => s.ctr);
  const cvrs   = series.map(s => s.cvr);
  const costs  = series.map(s => microsToYenNum(s.cost_micros));

  const chartDefaults = {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { color:'rgba(255,255,255,0.05)' }, ticks: { color:'#64748b', font:{size:11} } },
      y: { grid: { color:'rgba(255,255,255,0.05)' }, ticks: { color:'#64748b', font:{size:11} } },
    }
  };

  // CTR/CVR チャート
  if(perfChart) perfChart.destroy();
  perfChart = new Chart(document.getElementById('perfChart'), {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label:'CTR(%)', data: ctrs, borderColor:'#3b82f6', backgroundColor:'rgba(59,130,246,0.08)', tension:0.4, fill:true, pointRadius:3 },
        { label:'CVR(%)', data: cvrs, borderColor:'#10b981', backgroundColor:'rgba(16,185,129,0.08)', tension:0.4, fill:true, pointRadius:3 },
      ]
    },
    options: { ...chartDefaults }
  });

  // 費用チャート
  if(costChart) costChart.destroy();
  costChart = new Chart(document.getElementById('costChart'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label:'費用(円)', data: costs,
          backgroundColor:'rgba(139,92,246,0.6)', borderColor:'#8b5cf6',
          borderRadius: 6, borderWidth: 1 }
      ]
    },
    options: {
      ...chartDefaults,
      plugins: { legend: { display: false } },
    }
  });
}

function renderDashCampaigns(campaigns) {
  if(!campaigns || campaigns.length === 0) {
    document.getElementById('dashCampaignTable').innerHTML = '<p style="padding:24px;color:var(--text-3);text-align:center">キャンペーンがありません</p>';
    return;
  }
  document.getElementById('dashCampaignTable').innerHTML = `
    <table class="data-table">
      <thead><tr>
        <th>キャンペーン名</th><th>状態</th><th>表示回数</th>
        <th>CTR</th><th>平均CPC</th><th>費用</th><th>CV数</th>
      </tr></thead>
      <tbody>
        ${campaigns.map(c => `
          <tr>
            <td><strong>${c.name}</strong></td>
            <td><span class="status-badge ${c.status?.toLowerCase()}">${c.status}</span></td>
            <td>${fmtNum(c.impressions)}</td>
            <td><span style="color:${c.ctr>3?'#10b981':c.ctr>1?'#f59e0b':'#ef4444'}">${fmtPct(c.ctr)}</span></td>
            <td>${microsToYen(c.avg_cpc_micros)}</td>
            <td>${microsToYen(c.cost_micros)}</td>
            <td>${(c.conversions||0).toFixed(1)}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>`;
}

function updateMonitorStatus(status) {
  const dot = document.querySelector('.status-dot');
  const text = document.querySelector('.status-text');
  if(status?.running) {
    dot.className = 'status-dot online';
    text.textContent = `監視中 | ${status.last_check?.slice(11,16)||'-'}チェック`;
  } else {
    dot.className = 'status-dot offline';
    text.textContent = 'スケジューラ停止中';
  }
}

function updateMockBadge(mockMode) {
  document.getElementById('mockBadge').style.display = mockMode ? 'flex' : 'none';
}

// ============================================================
// キャンペーン管理
// ============================================================
async function loadCampaigns() {
  try {
    const data = await api(`/campaigns?clinic_id=${currentClinicId}`);
    const campaigns = data.campaigns || [];
    const wrap = document.getElementById('campaignsList');
    if(!campaigns.length) {
      wrap.innerHTML = `<div class="card"><div class="loading-state"><p>まだキャンペーンがありません。「新規キャンペーン自動生成」から始めましょう！</p></div></div>`;
      return;
    }
    wrap.innerHTML = campaigns.map(c => `
      <div class="campaign-item">
        <div class="campaign-header">
          <div class="campaign-name">${c.name}</div>
          <span class="status-badge ${c.status?.toLowerCase()}">${c.status}</span>
          ${c.status==='ENABLED'
            ? `<button class="btn btn-secondary" onclick="toggleCampaign('${c.id}','PAUSED')">一時停止</button>`
            : `<button class="btn btn-success" onclick="toggleCampaign('${c.id}','ENABLED')">再開</button>`}
        </div>
        <div class="campaign-stats">
          <div class="campaign-stat"><div class="campaign-stat-label">表示回数</div><div class="campaign-stat-value">${fmtNum(c.impressions)}</div></div>
          <div class="campaign-stat"><div class="campaign-stat-label">CTR</div><div class="campaign-stat-value" style="color:${c.ctr>3?'#10b981':c.ctr>1?'#f59e0b':'#ef4444'}">${fmtPct(c.ctr)}</div></div>
          <div class="campaign-stat"><div class="campaign-stat-label">平均CPC</div><div class="campaign-stat-value">${microsToYen(c.avg_cpc_micros)}</div></div>
          <div class="campaign-stat"><div class="campaign-stat-label">費用</div><div class="campaign-stat-value">${microsToYen(c.cost_micros)}</div></div>
          <div class="campaign-stat"><div class="campaign-stat-label">CV数</div><div class="campaign-stat-value">${(c.conversions||0).toFixed(1)}</div></div>
        </div>
      </div>
    `).join('');
  } catch(e) {
    toast('キャンペーンの読み込みに失敗: ' + e.message, 'error');
  }
}

async function toggleCampaign(id, status) {
  try {
    await api(`/campaigns/${id}/status?status=${status}&clinic_id=${currentClinicId}`, { method:'PATCH', body:'{}' });
    toast(`キャンペーンを${status==='ENABLED'?'再開':'一時停止'}しました`, 'success');
    loadCampaigns();
  } catch(e) {
    toast('更新失敗: ' + e.message, 'error');
  }
}
window.toggleCampaign = toggleCampaign;

document.getElementById('newCampaignBtn').addEventListener('click', () => {
  const categories = ['腰痛', '肩こり', '産後骨盤', '姿勢矯正', 'スポーツ'];
  showModal('新規キャンペーン自動生成', `
    <div class="form-group">
      <label>クリニック名</label>
      <input type="text" id="newClinicName" class="form-input" placeholder="〇〇整体院">
    </div>
    <div class="form-group">
      <label>地域</label>
      <input type="text" id="newRegion" class="form-input" placeholder="渋谷区">
    </div>
    <div class="form-group">
      <label>カテゴリ（訴求軸）</label>
      <select id="newCategory" class="form-input">
        ${categories.map(c => `<option value="${c}">${c}</option>`).join('')}
      </select>
    </div>
    <div class="form-group">
      <label>予算（円/日） ※後から変更可</label>
      <input type="number" id="newBudget" class="form-input" value="3000" min="1000" step="500">
    </div>
  `, `
    <button class="btn btn-secondary" onclick="closeModal()">キャンセル</button>
    <button class="btn btn-primary" id="confirmNewCampaign">自動生成</button>
  `);
  document.getElementById('confirmNewCampaign').addEventListener('click', async () => {
    const body = {
      clinic_id: currentClinicId,
      clinic_name: document.getElementById('newClinicName').value || '整体院',
      region: document.getElementById('newRegion').value || '',
      category: document.getElementById('newCategory').value,
      budget_yen: parseInt(document.getElementById('newBudget').value)||3000,
    };
    try {
      const res = await api('/campaigns', { method:'POST', body: JSON.stringify(body) });
      closeModal();
      toast(`キャンペーン「${res.campaign.name}」を作成しました。入札ルールも自動設定済みです。`, 'success', 5000);
      loadCampaigns();
    } catch(e) {
      toast('作成失敗: ' + e.message, 'error');
    }
  });
});

// ============================================================
// 予算設定（手動のみ）
// ============================================================
async function loadBudget() {
  try {
    const data = await api(`/campaigns?clinic_id=${currentClinicId}`);
    // DBキャンペーンがない（モックモード等）場合はAPIキャンペーンデータで代替
    const local = (data.local_campaigns && data.local_campaigns.length)
      ? data.local_campaigns
      : (data.campaigns || []);
    const wrap = document.getElementById('budgetList');
    if(!local.length) {
      wrap.innerHTML = '<div class="card"><p style="text-align:center;color:var(--text-3);padding:32px">まだキャンペーンがありません</p></div>';
      return;
    }
    wrap.innerHTML = `<div class="card">${local.map(c => {
      const budgetYen = microsToYenNum(c.budget_micros);
      const maxBudget = 10000;
      const pct = Math.min(100, Math.round(budgetYen/maxBudget*100));
      // IDはDBのid（数値）またはモックID（文字列）の両方を使う
      const safeId = encodeURIComponent(c.id);
      return `
        <div class="budget-item">
          <div class="budget-row">
            <div class="budget-name">${c.name}${c.status === 'PAUSED' ? ' <span class="status-badge warning">停止中</span>' : ''}</div>
            <div class="budget-input-wrap">
              <span class="budget-prefix">¥</span>
              <input type="number" class="budget-input" id="budget_${safeId}"
                value="${budgetYen}" min="0" step="500">
              <span class="budget-prefix">/日</span>
              <button class="btn btn-primary" onclick="saveBudget('${safeId}')">保存</button>
            </div>
          </div>
          <div class="budget-progress">
            <div class="progress-label">
              <span>現在: ${microsToYen(c.budget_micros)}/日</span>
              <span>${pct}% / 上限¥${maxBudget.toLocaleString()}</span>
            </div>
            <div class="progress-bar-wrap">
              <div class="progress-bar-fill" style="width:${pct}%"></div>
            </div>
          </div>
        </div>
      `;
    }).join('')}</div>`;
  } catch(e) {
    toast('予算データ読み込み失敗: ' + e.message, 'error');
  }
}

async function saveBudget(campaignId) {
  const budgetYen = parseInt(document.getElementById(`budget_${campaignId}`)?.value)||0;
  try {
    await api(`/budget/${campaignId}`, {
      method:'POST',
      body: JSON.stringify({ clinic_id: currentClinicId, budget_yen: budgetYen })
    });
    toast(`予算を¥${budgetYen.toLocaleString()}/日に設定しました`, 'success');
    loadBudget();
  } catch(e) {
    toast('保存失敗: ' + e.message, 'error');
  }
}
window.saveBudget = saveBudget;

// ============================================================
// 入札ルール
// ============================================================
const FIELD_LABELS = {
  ctr:'CTR(%)', cvr:'CVR(%)', avg_cpc:'平均CPC(円)',
  cost:'費用(円)', impressions:'表示回数', clicks:'クリック',
  conversions:'CV数', avg_ctr_7d:'7日平均CTR', avg_cvr_7d:'7日平均CVR',
};
const OP_LABELS = { gt:'>', gte:'>=', lt:'<', lte:'<=', eq:'=' };
const ACTION_LABELS = {
  increase_bid_pct: '入札増加',
  decrease_bid_pct: '入札減少',
  pause_campaign: 'キャンペーン停止',
};

async function loadBidRules() {
  try {
    const data = await api(`/bid-rules?clinic_id=${currentClinicId}`);
    const wrap = document.getElementById('bidRulesList');
    const rules = data.rules || [];
    if(!rules.length) {
      wrap.innerHTML = '<p style="color:var(--text-3);text-align:center;padding:40px">ルールがありません。追加してください。</p>';
      return;
    }
    wrap.innerHTML = rules.map(r => {
      const isDown = r.action.includes('decrease') || r.action.includes('pause');
      return `
        <div class="rule-item">
          <div style="font-size:24px">${r.enabled ? '✅' : '⏸'}</div>
          <div class="rule-info">
            <div class="rule-name">${r.name}</div>
            <div class="rule-cond">
              条件: ${FIELD_LABELS[r.condition_field]||r.condition_field}
              ${OP_LABELS[r.condition_op]||r.condition_op}
              ${r.condition_value}
            </div>
          </div>
          <span class="rule-action-badge ${isDown?'down':''}">
            ${ACTION_LABELS[r.action]||r.action} ${r.action_value}%
          </span>
          <button class="btn btn-danger" onclick="deleteBidRule(${r.id})">削除</button>
        </div>`;
    }).join('');
  } catch(e) {
    toast('入札ルール読み込み失敗: ' + e.message, 'error');
  }
}
window.deleteBidRule = async function(id) {
  if (!confirm('この入札ルールを削除しますか？')) return;
  try {
    await api(`/bid-rules/${id}?clinic_id=${currentClinicId}`, { method: 'DELETE' });
    toast('入札ルールを削除しました', 'success');
    loadBidRules();
  } catch(e) {
    toast('削除失敗: ' + e.message, 'error');
  }
};

document.getElementById('newBidRuleBtn').addEventListener('click', () => {
  showModal('入札ルール追加', `
    <div class="form-group">
      <label>ルール名</label>
      <input type="text" id="brName" class="form-input" placeholder="CTR低下時に入札を下げる">
    </div>
    <div class="form-group">
      <label>条件指標</label>
      <select id="brField" class="form-input">
        ${Object.entries(FIELD_LABELS).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}
      </select>
    </div>
    <div class="form-group">
      <label>条件式</label>
      <select id="brOp" class="form-input">
        ${Object.entries(OP_LABELS).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}
      </select>
    </div>
    <div class="form-group">
      <label>閾値</label>
      <input type="number" id="brValue" class="form-input" value="2.0" step="0.1">
    </div>
    <div class="form-group">
      <label>アクション</label>
      <select id="brAction" class="form-input">
        ${Object.entries(ACTION_LABELS).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}
      </select>
    </div>
    <div class="form-group">
      <label>変更量 (%)</label>
      <input type="number" id="brActionValue" class="form-input" value="10" min="1" max="30">
    </div>
    <div class="form-group">
      <label>最大調整幅 (%)</label>
      <input type="number" id="brMaxPct" class="form-input" value="20" min="1" max="30">
    </div>
  `, `
    <button class="btn btn-secondary" onclick="closeModal()">キャンセル</button>
    <button class="btn btn-primary" id="saveBidRule">保存</button>
  `);
  document.getElementById('saveBidRule').addEventListener('click', async () => {
    const body = {
      clinic_id: currentClinicId,
      name: document.getElementById('brName').value,
      condition_field: document.getElementById('brField').value,
      condition_op: document.getElementById('brOp').value,
      condition_value: parseFloat(document.getElementById('brValue').value),
      action: document.getElementById('brAction').value,
      action_value: parseFloat(document.getElementById('brActionValue').value),
      max_adjustment_pct: parseFloat(document.getElementById('brMaxPct').value),
    };
    try {
      await api('/bid-rules', { method:'POST', body: JSON.stringify(body) });
      closeModal();
      toast('入札ルールを保存しました', 'success');
      loadBidRules();
    } catch(e) {
      toast('保存失敗: ' + e.message, 'error');
    }
  });
});

document.getElementById('runBidNowBtn').addEventListener('click', async () => {
  try {
    await api(`/bid-rules/run-now?clinic_id=${currentClinicId}`, { method:'POST', body:'{}' });
    toast('入札調整を実行しました', 'success');
  } catch(e) {
    toast('実行失敗: ' + e.message, 'error');
  }
});

// ============================================================
// 過去データ解析＆AI最適化
// ============================================================
const uploadArea = document.getElementById('uploadArea');
const reportFileInput = document.getElementById('reportFile');

uploadArea.addEventListener('click', () => reportFileInput.click());
uploadArea.addEventListener('dragover', (e) => { e.preventDefault(); uploadArea.classList.add('dragover'); });
uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
uploadArea.addEventListener('drop', (e) => {
  e.preventDefault();
  uploadArea.classList.remove('dragover');
  if(e.dataTransfer.files && e.dataTransfer.files[0]) {
    handleReportUpload(e.dataTransfer.files[0]);
  }
});
reportFileInput.addEventListener('change', (e) => {
  if(e.target.files && e.target.files[0]) {
    handleReportUpload(e.target.files[0]);
  }
});

async function handleReportUpload(file) {
  const allowed = ['application/pdf', 'text/csv', 'application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'text/plain'];
  if(!allowed.includes(file.type) && !file.name.match(/\.(pdf|csv|xlsx|xls|txt)$/i)) {
    toast('PDF, CSV, Excel等のファイルを選択してください', 'error');
    return;
  }
  
  document.getElementById('analyzeLoading').style.display = 'flex';
  document.getElementById('analyzeResult').style.display = 'none';
  
  const fd = new FormData();
  fd.append('file', file);
  
  try {
    const res = await fetch(`${API}/analyze-report?clinic_id=${currentClinicId}`, {
      method: 'POST',
      body: fd
    });
    if(!res.ok) {
      const err = await res.json().catch(()=>({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    renderAnalyzeResult(data.analysis);
  } catch(e) {
    document.getElementById('analyzeLoading').style.display = 'none';
    toast('解析に失敗しました: ' + e.message, 'error');
  }
}

function renderAnalyzeResult(analysis) {
  document.getElementById('analyzeLoading').style.display = 'none';
  document.getElementById('analyzeResult').style.display = 'block';
  
  const okTags = (analysis.good_keywords||[]).map(k => `<span class="tag">${k}</span>`).join('');
  document.getElementById('arGoodTags').innerHTML = okTags || '<span style="color:var(--text-3);font-size:12px;">データなし</span>';
  
  // 悪いタグはクリックで除外KWに追加できる（CPA削減ワンクリック）
  const badKws = analysis.wasted_spend||[];
  if (badKws.length) {
    document.getElementById('arBadTags').innerHTML = badKws.map(k =>
      `<span class="tag" style="cursor:pointer;border:1px solid var(--danger);color:var(--danger)" title="クリックで除外KWに追加" onclick="addNegativeKeyword('${k.replace(/'/g,"\\'")}','BROAD','ai_analysis')">⊖ ${k}</span>`
    ).join('');
  } else {
    document.getElementById('arBadTags').innerHTML = '<span style="color:var(--text-3);font-size:12px;">データなし</span>';
  }

  document.getElementById('arTrends').textContent = analysis.demographic_trends || '';
  document.getElementById('arRec').textContent = analysis.recommendation || '';
  
  toast('解析が完了しました！除外推奨タグをクリックして除外KWに追加できます。', 'success', 5000);
}

document.getElementById('applyAnalyzeBtn').addEventListener('click', () => {
  // 新規キャンペーンモーダルを開き、解析結果（例えばキーワードや傾向）をカテゴリ等に反映する擬似導線
  switchPage('campaigns');
  setTimeout(() => document.getElementById('newCampaignBtn').click(), 300);
});

// ============================================================
// AI広告文生成
// ============================================================
document.getElementById('generateAdCopyBtn').addEventListener('click', async () => {
  const body = {
    clinic_id: currentClinicId,
    clinic_name: document.getElementById('acClinicName').value || '整体院',
    region: document.getElementById('acRegion').value || '',
    appeal_points: document.getElementById('acAppealPoints').value,
    target_issues: document.getElementById('acTargetIssues').value || '腰痛、肩こり',
    extra_instructions: document.getElementById('acExtra').value,
  };
  document.getElementById('adCopyResult').style.display = 'none';
  document.getElementById('adCopyLoading').style.display = 'block';
  try {
    const data = await api('/ad-copy/generate', { method:'POST', body: JSON.stringify(body) });
    document.getElementById('adCopyLoading').style.display = 'none';
    document.getElementById('adCopyResult').style.display = 'block';
    renderAdCopyPreview(data);
    loadAdCopyHistory();
    // ★ 心理トリガースコアを自動計算（生成直後に非同期実行）
    if (data.headlines?.length) {
      setTimeout(() => runPsychScore(data.headlines, data.descriptions || []), 500);
    }
  } catch(e) {
    document.getElementById('adCopyLoading').style.display = 'none';
    toast('広告文生成失敗: ' + e.message, 'error');
  }
});

function renderAdCopyPreview(data) {
  const headlines = data.headlines || [];
  const descs = data.descriptions || [];
  const previewH = headlines.slice(0,3).join(' | ');
  const previewD = descs[0] || '';
  const genBadge = data.generated_by === 'gemini'
    ? '<span style="font-size:11px;color:#a78bfa;margin-left:8px">✨ Gemini AI生成</span>'
    : '<span style="font-size:11px;color:var(--text-3);margin-left:8px">テンプレート使用</span>';

  // 一括コピー用データを保存
  window._lastAdCopyData = {headlines, descs};
  document.getElementById('adPreviewBox').innerHTML = `
    <div style="margin-bottom:12px;display:flex;align-items:center;justify-content:space-between">
      <div style="display:flex;align-items:center"><strong style="font-size:13px">RSAプレビュー</strong>${genBadge}</div>
      <button onclick="copyAllAdCopy()" style="font-size:11px;font-weight:700;padding:6px 16px;border:1px solid rgba(255,255,255,0.2);background:rgba(255,255,255,0.06);color:#fff;border-radius:99px;cursor:pointer;letter-spacing:1px">📋 一括コピー</button>
    </div>
    <div class="rsa-preview">
      <div class="rsa-url">example.com › 整体院 › 予約</div>
      <div class="rsa-headline">${previewH}</div>
      <div class="rsa-desc">${previewD}</div>
    </div>
    <div class="headlines-list">
      <h4>見出し（${headlines.length}個）</h4>
      ${headlines.map((h,i) => `<span class="headline-chip" data-copy="${h.replace(/"/g,'&quot;')}" data-copy-label="見出し${i+1}" title="クリックでコピー" style="cursor:pointer">${h}</span>`).join('')}
    </div>
    <div class="descs-list" style="margin-top:12px">
      <h4>説明文（${descs.length}個）</h4>
      ${descs.map((d,i) => `<div class="desc-item" data-copy="${d.replace(/"/g,'&quot;')}" data-copy-label="説明文${i+1}" style="cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:12px"><span>${d}</span><span style="font-size:10px;color:var(--text-3);white-space:nowrap">📋</span></div>`).join('')}
    </div>
  `;
}

document.getElementById('applyAdCopyBtn').addEventListener('click', () => {
  toast('Google広告への反映にはAPIキーの設定が必要です。設定画面で本番モードに切替後に有効になります。', 'info', 5000);
});

async function loadAdCopyHistory() {
  try {
    const data = await api(`/ad-copies?clinic_id=${currentClinicId}`);
    const copies = data.ad_copies || [];
    const histEl = document.getElementById('adCopyHistory');
    if (!copies.length) {
      histEl.innerHTML = '<p style="color:var(--text-3);text-align:center;padding:24px">まだ広告文が生成されていません</p>';
      return;
    }
    histEl.innerHTML = `<table class="data-table">
        <thead><tr><th>#</th><th>生成日時</th><th>ステータス</th><th>見出し（先頭）</th><th>操作</th></tr></thead>
        <tbody>${copies.map(c => `
          <tr>
            <td>${c.id}</td>
            <td>${fmtDate(c.created_at)}</td>
            <td><span class="status-badge ${c.status === 'retired' ? 'warning' : 'info'}">${c.status === 'retired' ? '廃案' : c.status}</span></td>
            <td style="color:var(--text-2);font-size:12px">${(c.headlines||'').split('\n')[0]||'-'}</td>
            <td style="display:flex;gap:6px">
              ${c.status !== 'retired' ? `
                <button class="btn btn-ghost" style="font-size:11px;padding:3px 8px" onclick="setAbTestWinner(${c.id})">🏆 A/B採用</button>
                <button class="btn btn-ghost" style="font-size:11px;padding:3px 8px;color:var(--danger)" onclick="retireAdCopy(${c.id})">🗑 廃案</button>
              ` : '<span style="font-size:11px;color:var(--text-3)">廃案済み</span>'}
            </td>
          </tr>`).join('')}
        </tbody>
      </table>`;
  } catch(e) {}
}

// ============================================================
// アラート
// ============================================================
async function loadAlerts() {
  try {
    const data = await api(`/alerts?clinic_id=${currentClinicId}`);
    const alerts = data.alerts || [];
    const wrap = document.getElementById('alertsList');
    const icons = { ERROR:'🚨', WARNING:'⚠️', INFO:'ℹ️' };
    if(!alerts.length) {
      wrap.innerHTML = '<div class="card"><p style="text-align:center;color:var(--text-3);padding:32px">アラートはありません ✅</p></div>';
      return;
    }
    wrap.innerHTML = `<div class="card" style="padding:0">
      ${alerts.map(a => `
        <div class="alert-item">
          <div class="alert-level-icon">${icons[a.level]||'📌'}</div>
          <div>
            <div class="alert-msg">${a.message}</div>
            <div class="alert-time">${fmtDate(a.created_at)}</div>
          </div>
          <span class="status-badge ${a.level.toLowerCase()}">${a.level}</span>
        </div>`).join('')}
    </div>`;
  } catch(e) {
    toast('アラート読み込み失敗: ' + e.message, 'error');
  }
}

document.getElementById('checkNowBtn').addEventListener('click', async () => {
  try {
    await api(`/monitor/check-now?clinic_id=${currentClinicId}`, { method:'POST', body:'{}' });
    toast('チェックを実行しました', 'success');
    setTimeout(loadAlerts, 1000);
  } catch(e) {
    toast('実行失敗: ' + e.message, 'error');
  }
});

// ============================================================
// 設定
// ============================================================
async function loadSettings() {
  try {
    const data = await api(`/settings?clinic_id=${currentClinicId}`);
    const s = data.settings || {};
    document.getElementById('settCustomerId').value = s.customer_id || '';
    document.getElementById('settDevToken').value   = s.developer_token === '***設定済み***' ? '' : (s.developer_token||'');
    document.getElementById('settDevToken').placeholder = s.developer_token === '***設定済み***' ? '***設定済み（変更する場合のみ入力）***' : '（取得後に入力）';
    document.getElementById('settClientId').value     = s.client_id || '';
    document.getElementById('settMockMode').value     = s.mock_mode != null ? String(s.mock_mode) : '1';
    document.getElementById('settLineToken').value    = '';
    document.getElementById('settLineUserId').value   = s.line_user_id || '';
    document.getElementById('settPersonaAgeGender').value = s.target_age_gender || '';
    document.getElementById('settPersonaJob').value = s.target_job_lifestyle || '';
    document.getElementById('settPersonaPainPoint').value = s.target_pain_point || '';
    document.getElementById('settPersonaDesiredOutcome').value = s.target_desired_outcome || '';
    // メール設定
    document.getElementById('settNotifyEmail').value = s.notification_email || '';
    document.getElementById('settSmtpUser').value    = s.smtp_user || '';
    // パスワードは表示しない
    document.getElementById('settSmtpPass').value    = '';
    document.getElementById('settSmtpPass').placeholder = s.smtp_pass_set ? '••••••••（変更時のみ入力）' : '16桁のアプリパスワード';
    // GA4設定
    const ga4El = document.getElementById('settGa4PropertyId');
    if (ga4El) ga4El.value = s.ga4_property_id || '';
    // 月予算
    const budgetEl = document.getElementById('settMonthlyBudget');
    if (budgetEl) budgetEl.value = s.monthly_budget_yen || 300000;
    monthlyBudgetYen = s.monthly_budget_yen || 300000;
  } catch(e) {
    toast('設定読み込み失敗: ' + e.message, 'error');
  }
}

document.getElementById('saveSettingsBtn').addEventListener('click', async () => {
  const body = {
    clinic_id: currentClinicId,
    customer_id: document.getElementById('settCustomerId').value || null,
    mock_mode: parseInt(document.getElementById('settMockMode').value),
    line_user_id: document.getElementById('settLineUserId').value || null,
    target_age_gender: document.getElementById('settPersonaAgeGender').value || null,
    target_job_lifestyle: document.getElementById('settPersonaJob').value || null,
    target_pain_point: document.getElementById('settPersonaPainPoint').value || null,
    target_desired_outcome: document.getElementById('settPersonaDesiredOutcome').value || null,
    notification_email: document.getElementById('settNotifyEmail').value || null,
    smtp_user: document.getElementById('settSmtpUser').value || null,
    ga4_property_id: document.getElementById('settGa4PropertyId')?.value || null,
    monthly_budget_yen: parseInt(document.getElementById('settMonthlyBudget')?.value || '300000') || 300000,
  };
  const devToken  = document.getElementById('settDevToken').value;
  const lineToken = document.getElementById('settLineToken').value;
  const smtpPass  = document.getElementById('settSmtpPass').value;
  if(devToken)  body.developer_token  = devToken;
  if(lineToken) body.line_channel_token = lineToken;
  if(smtpPass)  body.smtp_pass = smtpPass;

  try {
    await api('/settings', { method:'POST', body: JSON.stringify(body) });
    toast('設定を保存しました ✅', 'success');
  } catch(e) {
    toast('保存失敗: ' + e.message, 'error');
  }
});

// ---- パスワード変更 ----
window.doChangePassword = async function() {
  const current  = document.getElementById('changePwCurrent').value;
  const newPw    = document.getElementById('changePwNew').value;
  const confirm  = document.getElementById('changePwConfirm').value;
  if (!current || !newPw || !confirm) { toast('全項目を入力してください', 'error'); return; }
  if (newPw !== confirm) { toast('新しいパスワードが一致しません', 'error'); return; }
  if (newPw.length < 8) { toast('パスワードは8文字以上にしてください', 'error'); return; }
  const btn = document.getElementById('changePwBtn');
  btn.disabled = true;
  try {
    const res = await api('/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ current_password: current, new_password: newPw })
    });
    toast('パスワードを変更しました。次回ログインから新しいパスワードをご使用ください。', 'success', 5000);
    document.getElementById('changePwCurrent').value = '';
    document.getElementById('changePwNew').value = '';
    document.getElementById('changePwConfirm').value = '';
  } catch(e) {
    toast('変更失敗: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
  }
};

document.getElementById('testLineBtn').addEventListener('click', async () => {
  try {
    await api('/line/test', {
      method:'POST',
      body: JSON.stringify({ clinic_id: currentClinicId, message: '✅ テスト送信です。LINE通知が正常に設定されています。' })
    });
    toast('LINEにテストメッセージを送信しました', 'success');
  } catch(e) {
    toast('LINE送信失敗: ' + e.message + ' （LINE設定を確認してください）', 'error');
  }
});

document.getElementById('testEmailBtn').addEventListener('click', async () => {
  const email = document.getElementById('settNotifyEmail').value.trim();
  if (!email) { toast('通知先メールアドレスを入力してください', 'error'); return; }
  try {
    const d = await api('/settings/test-email', {
      method:'POST',
      body: JSON.stringify({ clinic_id: currentClinicId, test_email: email })
    });
    toast(d.message || 'テストメールを送信しました ✅', d.success ? 'success' : 'info');
  } catch(e) {
    toast('メール送信失敗: ' + e.message, 'error');
  }
});

// ——— PDFレポート出力 ———
document.getElementById('exportPdfBtn').addEventListener('click', async () => {
  const btn = document.getElementById('exportPdfBtn');
  btn.textContent = '生成中...';
  btn.disabled = true;
  try {
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF({ orientation: 'portrait', unit: 'pt', format: 'a4' });
    const today = new Date().toLocaleDateString('ja-JP');
    const clinicName = document.querySelector('#clinicSelect option:checked')?.textContent || 'クリニック';

    // タイトル
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(20);
    doc.setTextColor(59, 130, 246);
    doc.text('\u5e83\u544a\u904b\u7528\u30ec\u30dd\u30fc\u30c8', 40, 55);
    doc.setFontSize(11);
    doc.setTextColor(100, 116, 139);
    doc.text(`${clinicName}  /  ${today}時点`, 40, 75);

    // KPIデータ取得
    const dashData = lastData || {};
    const s = dashData.summary || {};
    const cost  = Math.round((s.total_cost_micros||0)/1e6).toLocaleString();
    const clicks = (s.total_clicks||0).toLocaleString();
    const cvs    = (s.total_conversions||0).toFixed(1);
    const ctr    = ((s.avg_ctr||0)*100).toFixed(2);
    const cpa    = s.total_conversions > 0
      ? Math.round((s.total_cost_micros||0)/1e6 / s.total_conversions).toLocaleString()
      : '-';

    doc.setDrawColor(226, 232, 240);
    doc.line(40, 88, 555, 88);

    // KPIボックス
    const kpiBoxes = [
      { label: '総費用（7日）', value: `¥${cost}` },
      { label: 'クリック数',    value: clicks },
      { label: 'CV数',         value: `${cvs}件` },
      { label: 'CTR',           value: `${ctr}%` },
      { label: 'CPA',           value: `¥${cpa}` },
    ];
    doc.setFontSize(9);
    doc.setTextColor(100,116,139);
    kpiBoxes.forEach((k, i) => {
      const x = 40 + i * 103;
      doc.setFillColor(30, 41, 59);
      doc.roundedRect(x, 95, 98, 52, 4, 4, 'F');
      doc.setTextColor(148,163,184);
      doc.setFontSize(8);
      doc.text(k.label, x+6, 113);
      doc.setTextColor(241,245,249);
      doc.setFontSize(13);
      doc.setFont('helvetica','bold');
      doc.text(k.value, x+6, 133);
      doc.setFont('helvetica','normal');
    });

    // 予測を取得して追記
    let forecastY = 170;
    try {
      const fc = await api(`/forecast?clinic_id=${currentClinicId}`);
      doc.setFontSize(13);
      doc.setFont('helvetica','bold');
      doc.setTextColor(59,130,246);
      doc.text('月末成果予測', 40, forecastY);
      forecastY += 14;
      doc.setFontSize(9);
      doc.setFont('helvetica','normal');
      doc.setTextColor(100,116,139);
      doc.text(`予測費用: ¥${(fc.projected_cost_yen||0).toLocaleString()}  |  予測CV: ${fc.projected_conversions||0}件  |  予測CPA: ¥${(fc.projected_cpa_yen||0).toLocaleString()}`, 40, forecastY);
      forecastY += 24;
    } catch(_) { forecastY = 170; }

    // キャンペーン一覧
    doc.setFontSize(13);
    doc.setFont('helvetica','bold');
    doc.setTextColor(59,130,246);
    doc.text('キャンペーン別パフォーマンス', 40, forecastY + 8);
    forecastY += 24;

    const campaigns = dashData.campaigns || [];
    doc.setFontSize(8);
    doc.setFont('helvetica', 'normal');
    const headers = ['名前', 'クリック', '表示', 'CTR', 'CV', '費用'];
    const colW    = [175, 60, 70, 60, 55, 75];
    let cx = 40;
    doc.setTextColor(100,116,139);
    headers.forEach((h, i) => { doc.text(h, cx, forecastY); cx += colW[i]; });
    forecastY += 3;
    doc.setDrawColor(51,65,85);
    doc.line(40, forecastY, 555, forecastY);
    forecastY += 10;

    doc.setTextColor(241,245,249);
    campaigns.slice(0,12).forEach(c => {
      cx = 40;
      const row = [
        (c.name||'').slice(0,22),
        (c.clicks||0).toString(),
        (c.impressions||0).toLocaleString(),
        `${((c.ctr||0)*100).toFixed(1)}%`,
        (c.conversions||0).toFixed(1),
        `¥${Math.round((c.cost_micros||0)/1e6).toLocaleString()}`,
      ];
      doc.setFillColor(30,41,59);
      doc.rect(40, forecastY-8, 515, 14, 'F');
      row.forEach((v, i) => { doc.text(v, cx, forecastY); cx += colW[i]; });
      forecastY += 16;
      if (forecastY > 760) { doc.addPage(); forecastY = 40; }
    });

    // フッター
    doc.setFontSize(8);
    doc.setTextColor(71,85,105);
    doc.text(`広告運用AI ● 自動生成レポート ● 出力日時: ${today}`, 40, 800);

    doc.save(`広告レポート_${today.replaceAll('/','-')}.pdf`);
    toast('PDFレポートをダウンロードしました ✅', 'success');
  } catch(e) {
    toast('PDF生成失敗: ' + e.message, 'error');
    console.error(e);
  } finally {
    btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> PDFレポート出力';
    btn.disabled = false;
  }
});

// ============================================================
// 初期化
// ============================================================
async function init() {
  await loadClinics();
  await loadDashboard();

  // 5分毎にダッシュボードを自動更新
  setInterval(() => {
    const activePage = document.querySelector('.nav-item.active')?.dataset.page;
    if(activePage === 'dashboard') loadDashboard();
  }, 5 * 60 * 1000);
}

// ============================================================
// 除外KW管理 (Phase A: CPA削減)
// ============================================================
async function loadNegativeKeywords() {
  try {
    const data = await api(`/negative-keywords?clinic_id=${currentClinicId}`);
    const kws = data.negative_keywords || [];
    const wrap = document.getElementById('nkwList');
    if (!kws.length) {
      wrap.innerHTML = '<div class="card"><p style="text-align:center;color:var(--text-3);padding:32px">除外キーワードはまだ登録されていません</p></div>';
      return;
    }
    const matchLabels = { BROAD:'部分一致', PHRASE:'フレーズ一致', EXACT:'完全一致' };
    wrap.innerHTML = `<div class="card" style="padding:0">
      <table class="data-table">
        <thead><tr><th>#</th><th>キーワード</th><th>マッチタイプ</th><th>ソース</th><th>適用状態</th><th>追加日</th><th></th></tr></thead>
        <tbody>${kws.map(k => `
          <tr>
            <td>${k.id}</td>
            <td><strong>${k.keyword}</strong></td>
            <td><span class="status-badge info">${matchLabels[k.match_type]||k.match_type}</span></td>
            <td style="color:var(--text-3);font-size:12px">${k.source === 'ai_analysis' ? '🤖 AI解析' : '✋ 手動'}</td>
            <td><span class="status-badge ${k.applied ? 'success' : 'warning'}">${k.applied ? '適用済み' : '保留中'}</span></td>
            <td style="color:var(--text-3);font-size:12px">${fmtDate(k.created_at)}</td>
            <td><button class="btn btn-sm btn-secondary" onclick="deleteNkw(${k.id})">🗑 削除</button></td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
  } catch(e) {
    toast('除外KW読み込み失敗: ' + e.message, 'error');
  }
}

async function addNegativeKeyword(keyword, matchType='BROAD', source='manual') {
  if (!keyword.trim()) return;
  try {
    const res = await api('/negative-keywords', {
      method: 'POST',
      body: JSON.stringify({ clinic_id: currentClinicId, keyword: keyword.trim(), match_type: matchType, source })
    });
    toast(res.message || `「${keyword}」を除外リストに追加しました`, 'success');
    await loadNegativeKeywords();
  } catch(e) {
    toast('追加失敗: ' + e.message, 'error');
  }
}

window.deleteNkw = async function(id) {
  if (!confirm('この除外キーワードを削除しますか？')) return;
  try {
    await api(`/negative-keywords/${id}?clinic_id=${currentClinicId}`, { method: 'DELETE' });
    toast('除外キーワードを削除しました', 'success');
    await loadNegativeKeywords();
  } catch(e) {
    toast('削除失敗: ' + e.message, 'error');
  }
};

document.getElementById('addNkwBtn').addEventListener('click', () => {
  const kw = document.getElementById('nkwInput').value;
  const mt = document.getElementById('nkwMatchType').value;
  if (!kw.trim()) { toast('キーワードを入力してください', 'warning'); return; }
  addNegativeKeyword(kw, mt, 'manual');
  document.getElementById('nkwInput').value = '';
});

document.getElementById('nkwInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') document.getElementById('addNkwBtn').click();
});

// ============================================================
// A/Bテスト: 廃案ボタン
// ============================================================
window.retireAdCopy = async function(id) {
  if (!confirm('この広告文を廃案にしますか？')) return;
  try {
    await api(`/ad-copy/${id}/retire?clinic_id=${currentClinicId}`, { method: 'POST' });
    toast('広告文を廃案にしました', 'success');
    await loadAdCopyHistory();
  } catch(e) {
    toast('廃案失敗: ' + e.message, 'error');
  }
};

// A/Bテスト採用: 選んだ広告文を「winner」として採用し他を廃案
window.setAbTestWinner = async function(id) {
  if (!confirm('この広告文をA/Bテストの採用版にしますか？\n他の有効な広告文は廃案になります。')) return;
  try {
    const data = await api(`/ad-copies?clinic_id=${currentClinicId}`);
    const copies = (data.ad_copies || []).filter(c => c.status !== 'retired' && c.id !== id);
    for (const c of copies) {
      await api(`/ad-copy/${c.id}/retire?clinic_id=${currentClinicId}`, { method: 'POST' }).catch(() => {});
    }
    toast(`広告文 #${id} をA/Bテスト採用版に設定しました 🏆`, 'success', 4000);
    await loadAdCopyHistory();
  } catch(e) {
    toast('A/Bテスト設定失敗: ' + e.message, 'error');
  }
};

// A/Bテスト自動スコアリング（AI判定）
window.runAutoAbScore = async function(btn) {
  if (!btn) return;
  const origText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '⏳ AI判定中...';
  try {
    const d = await api(`/ab-test/auto-score?clinic_id=${currentClinicId}`, { method: 'POST' });
    if (!d.success) { toast('スコアリング失敗', 'error'); return; }
    if (d.recommendations.length === 0) {
      toast('現時点では廃案推奨の広告文はありません。引き続きデータを蓄積してください。', 'info', 4000);
    } else {
      const msgs = d.recommendations.map(r =>
        `グループ「${r.variant_group}」: 広告文#${r.loser_id}を廃案推奨（Winner:#${r.winner_id}）`
      ).join('\n');
      toast(`🤖 A/B判定完了: ${d.retired_candidates}件の廃案推奨を検出\n\n${msgs}`, 'warning', 8000);
      await loadAdCopyHistory();
    }
  } catch(e) {
    toast('エラー: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = origText;
  }
};

// ============================================================
// ---- ペルソナ管理 ----
// ============================================================
let _editPersonaId = null;

async function loadPersonas() {
  // ボタンバインド
  document.getElementById('addPersonaBtn').onclick = () => {
    _editPersonaId = null;
    ['pName','pAgeGender','pJob','pPain','pGoal'].forEach(id => {
      const el = document.getElementById(id);
      if(el) el.value = '';
    });
    const form = document.getElementById('personaForm');
    form.style.display = form.style.display === 'none' ? '' : 'none';
    document.getElementById('personaCampaignPanel').style.display = 'none';
  };
  document.getElementById('savePersonaBtn').onclick = savePersona;

  try {
    const data = await api(`/personas?clinic_id=${currentClinicId}`);
    renderPersonaList(data.personas || []);
  } catch(e) {
    toast('ペルソナ読み込み失敗: ' + e.message, 'error');
  }
}

function renderPersonaList(personas) {
  const wrap = document.getElementById('personaList');
  if(!personas.length) {
    wrap.innerHTML = `<div class="card"><p style="text-align:center;color:var(--text-3);padding:32px">
      まだペルソナがありません。「ペルソナを追加」ボタンで作成してください。</p></div>`;
    return;
  }
  wrap.innerHTML = personas.map(p => `
    <div class="card" style="margin-bottom:12px;border-left:4px solid #7c3aed">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div style="flex:1">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
            <span style="font-size:16px;font-weight:700">🎭 ${p.name}</span>
            ${p.is_default ? '<span class="badge-ai" style="background:#7c3aed">デフォルト</span>' : ''}
          </div>
          <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:4px">
            ${p.age_gender ? `<div style="font-size:12px;color:var(--text-2)">👤 ${p.age_gender}</div>` : ''}
            ${p.job_lifestyle ? `<div style="font-size:12px;color:var(--text-2)">💼 ${p.job_lifestyle}</div>` : ''}
            ${p.pain_point ? `<div style="font-size:12px;color:var(--text-2);grid-column:span 2">😣 ${p.pain_point}</div>` : ''}
            ${p.desired_outcome ? `<div style="font-size:12px;color:var(--text-3);grid-column:span 2">✨ ${p.desired_outcome}</div>` : ''}
          </div>
        </div>
        <div style="display:flex;gap:6px;margin-left:12px;flex-shrink:0">
          <button class="btn btn-ghost" style="font-size:12px;padding:4px 10px" onclick="editPersona(${p.id})">✏️ 編集</button>
          <button class="btn btn-ghost" style="font-size:12px;padding:4px 10px" onclick="showCampaignLinkPanel(${p.id},'${p.name.replace(/'/g,"\\'")}')">📎 KW紐付け</button>
          <button class="btn btn-danger" style="font-size:12px;padding:4px 10px" onclick="deletePersona(${p.id})">🗑</button>
        </div>
      </div>
    </div>
  `).join('');
}

async function savePersona() {
  const name = document.getElementById('pName')?.value?.trim();
  if(!name) { toast('ペルソナ名は必須です', 'error'); return; }
  const body = {
    clinic_id: currentClinicId,
    name,
    age_gender: document.getElementById('pAgeGender')?.value?.trim() || null,
    job_lifestyle: document.getElementById('pJob')?.value?.trim() || null,
    pain_point: document.getElementById('pPain')?.value?.trim() || null,
    desired_outcome: document.getElementById('pGoal')?.value?.trim() || null,
  };
  try {
    if(_editPersonaId) {
      await api(`/personas/${_editPersonaId}`, { method:'PUT', body: JSON.stringify(body) });
      toast('ペルソナを更新しました', 'success');
    } else {
      await api('/personas', { method:'POST', body: JSON.stringify(body) });
      toast('ペルソナを追加しました', 'success');
    }
    document.getElementById('personaForm').style.display = 'none';
    _editPersonaId = null;
    await loadPersonas();
  } catch(e) {
    toast('保存失敗: ' + e.message, 'error');
  }
}

window.editPersona = async function(id) {
  _editPersonaId = id;
  try {
    const data = await api(`/personas?clinic_id=${currentClinicId}`);
    const p = (data.personas || []).find(x => x.id === id);
    if(!p) return;
    document.getElementById('pName').value = p.name || '';
    document.getElementById('pAgeGender').value = p.age_gender || '';
    document.getElementById('pJob').value = p.job_lifestyle || '';
    document.getElementById('pPain').value = p.pain_point || '';
    document.getElementById('pGoal').value = p.desired_outcome || '';
    document.getElementById('personaForm').style.display = '';
    document.getElementById('personaForm').scrollIntoView({behavior:'smooth'});
  } catch(e) { toast('読み込み失敗', 'error'); }
};

window.deletePersona = async function(id) {
  if(!confirm('このペルソナを削除しますか？キャンペーンとの紐付けも解除されます。')) return;
  try {
    await api(`/personas/${id}?clinic_id=${currentClinicId}`, { method:'DELETE' });
    toast('ペルソナを削除しました', 'success');
    loadPersonas();
  } catch(e) { toast('削除失敗: ' + e.message, 'error'); }
};

window.showCampaignLinkPanel = async function(personaId, personaName) {
  const panel = document.getElementById('personaCampaignPanel');
  document.getElementById('personaCampaignTitle').textContent = `📎 「${personaName}」のキャンペーン紐付け`;
  panel.style.display = '';
  panel.scrollIntoView({behavior:'smooth'});

  try {
    const [campaignData, linkedData] = await Promise.all([
      api(`/campaigns?clinic_id=${currentClinicId}`),
      api(`/campaigns/ALL/personas?clinic_id=${currentClinicId}`).catch(() => ({personas:[]}))
    ]);
    // 全キャンペーン（モック含む）
    const allCampaigns = [
      ...(campaignData.campaigns || []),
      ...(campaignData.local_campaigns || [])
    ].filter((c, i, arr) => arr.findIndex(x => x.id === c.id) === i);

    // このペルソナに紐付いているcampaign_idを取得
    const linkedIds = new Set();
    for(const c of allCampaigns) {
      try {
        const res = await api(`/campaigns/${c.id}/personas?clinic_id=${currentClinicId}`);
        if((res.personas||[]).some(p => p.id === personaId)) linkedIds.add(String(c.id));
      } catch(_) {}
    }

    const checkboxWrap = document.getElementById('personaCampaignCheckboxes');
    checkboxWrap.innerHTML = allCampaigns.map(c => `
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer;padding:6px 0;border-bottom:1px solid var(--border)">
        <input type="checkbox" id="cp_${c.id}" ${linkedIds.has(String(c.id)) ? 'checked' : ''}>
        <span>${c.name}</span>
        <span style="font-size:11px;color:var(--text-3)">${c.status||''}</span>
      </label>
    `).join('');

    document.getElementById('saveCampaignLinkBtn').onclick = async () => {
      for(const c of allCampaigns) {
        const checked = document.getElementById(`cp_${c.id}`)?.checked;
        const wasLinked = linkedIds.has(String(c.id));
        if(checked && !wasLinked) {
          await api(`/campaigns/${c.id}/personas/${personaId}?clinic_id=${currentClinicId}`, {method:'POST'}).catch(()=>{});
        } else if(!checked && wasLinked) {
          await api(`/campaigns/${c.id}/personas/${personaId}?clinic_id=${currentClinicId}`, {method:'DELETE'}).catch(()=>{});
        }
      }
      toast('紐付けを保存しました ✅', 'success');
      panel.style.display = 'none';
    };
  } catch(e) {
    toast('キャンペーン読み込み失敗: ' + e.message, 'error');
  }
};

// ============================================================
// ---- Phase 2A: 成果予測をダッシュボードに表示 ----
// ============================================================
async function loadForecast() {
  const wrap = document.getElementById('forecastSection');
  if (!wrap) return;
  try {
    const d = await api(`/forecast?clinic_id=${currentClinicId}`);
    wrap.innerHTML = `
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:16px">
        <div class="kpi-card" style="border-left:3px solid #8b5cf6">
          <div class="kpi-label">📅 月末予測費用</div>
          <div class="kpi-value">¥${(d.projected_cost_yen||0).toLocaleString()}</div>
          <div class="kpi-trend" style="color:#8b5cf6">日平均 ¥${(d.daily_avg_cost||0).toLocaleString()}</div>
        </div>
        <div class="kpi-card" style="border-left:3px solid #10b981">
          <div class="kpi-label">📈 月末予測CV数</div>
          <div class="kpi-value">${d.projected_conversions || 0}件</div>
          <div class="kpi-trend" style="color:#10b981">日平均 ${d.daily_avg_cv || 0}件</div>
        </div>
        <div class="kpi-card" style="border-left:3px solid #f59e0b">
          <div class="kpi-label">💰 予測CPA</div>
          <div class="kpi-value">¥${(d.projected_cpa_yen||0).toLocaleString()}</div>
          <div class="kpi-trend" style="color:#f59e0b">${d.elapsed_days}日分のデータから算出</div>
        </div>
      </div>
    `;
  } catch(e) {
    if (wrap) wrap.innerHTML = '';
  }
}

// ============================================================
// ---- Phase 2C: LP診断AI ----
// ============================================================
function loadLpDiag() {
  const btn = document.getElementById('lpDiagBtn');
  if (btn) btn.onclick = runLpDiag;
}

async function runLpDiag() {
  const url = document.getElementById('lpUrl')?.value?.trim();
  if (!url) { toast('URLを入力してください', 'error'); return; }
  const wrap = document.getElementById('lpResults');
  wrap.innerHTML = '<div class="card"><p style="text-align:center;padding:24px;color:var(--text-2)">🔍 AIが分析中...</p></div>';
  try {
    const d = await api('/lp-diagnosis', {
      method: 'POST',
      body: JSON.stringify({ clinic_id: currentClinicId, lp_url: url })
    });
    if (!d.success) { wrap.innerHTML = `<div class="card"><p style="color:var(--danger)">${d.error}</p></div>`; return; }
    const impactColor = { '高': '#ef4444', '中': '#f59e0b', '低': '#10b981' };
    wrap.innerHTML = `
      <div class="card">
        <div class="card-title">🔬 診断結果: ${url}</div>
        ${d.fetch_error ? `<div style="font-size:12px;color:var(--warning);margin-bottom:12px">⚠ URL取得エラー: ${d.fetch_error}（AIがパターンベースで分析しました）</div>` : ''}
        ${(d.suggestions||[]).map(s => `
          <div style="margin-bottom:12px;padding:12px;background:#0f172a;border-radius:8px;border-left:3px solid ${impactColor[s.impact]||'#3b82f6'}">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
              <span style="font-size:12px;color:${impactColor[s.impact]};font-weight:700">影響度: ${s.impact}</span>
              <span style="font-size:12px;color:var(--text-3)">${s.category}</span>
              <span style="font-size:12px;background:#1e293b;border-radius:4px;padding:1px 6px">優先度${s.priority}</span>
            </div>
            <div style="font-size:13px;font-weight:600;margin-bottom:4px">❌ ${s.issue}</div>
            <div style="font-size:13px;color:var(--text-2)">✅ ${s.suggestion}</div>
          </div>
        `).join('')}
      </div>
    `;
  } catch(e) { wrap.innerHTML = `<div class="card"><p style="color:var(--danger)">エラー: ${e.message}</p></div>`; }
}

// ============================================================
// ---- Phase 2C: KW提案AI ----
// ============================================================
function loadKwSuggest() {
  const btn = document.getElementById('kwSuggestBtn');
  if (btn) btn.onclick = runKwSuggest;
}

async function runKwSuggest() {
  const area = document.getElementById('kwArea')?.value?.trim();
  const service = document.getElementById('kwService')?.value?.trim() || '整体院';
  const wrap = document.getElementById('kwResults');
  wrap.innerHTML = '<div class="card"><p style="text-align:center;padding:24px;color:var(--text-2)">💡 AIが提案を生成中...</p></div>';
  try {
    const d = await api('/keyword-suggest', {
      method: 'POST',
      body: JSON.stringify({ clinic_id: currentClinicId, area, service_type: service })
    });
    if (!d.success) { wrap.innerHTML = `<div class="card"><p style="color:var(--danger)">${d.error}</p></div>`; return; }
    const kws = d.keywords || [];
    const prioColor = { '高': '#ef4444', '中': '#f59e0b', '低': '#10b981' };
    wrap.innerHTML = `
      <div class="card">
        <div class="card-title">💡 AIキーワード提案 ${kws.length}件</div>
        <table style="width:100%;border-collapse:collapse">
          <thead><tr>
            <th style="text-align:left;padding:8px;font-size:11px;color:var(--text-3)">キーワード</th>
            <th style="padding:8px;font-size:11px;color:var(--text-3)">タイプ</th>
            <th style="padding:8px;font-size:11px;color:var(--text-3)">検索意図</th>
            <th style="padding:8px;font-size:11px;color:var(--text-3)">月間数</th>
            <th style="padding:8px;font-size:11px;color:var(--text-3)">優先度</th>
            <th style="padding:8px;font-size:11px;color:var(--text-3)">操作</th>
          </tr></thead>
          <tbody>
            ${kws.map(k => `<tr style="border-top:1px solid var(--border)">
              <td style="padding:8px;font-weight:600;font-size:13px">${k.keyword}</td>
              <td style="padding:8px;text-align:center"><span style="background:#1e293b;padding:2px 6px;border-radius:4px;font-size:11px">${k.match_type}</span></td>
              <td style="padding:8px;font-size:12px;color:var(--text-2)">${k.intent}</td>
              <td style="padding:8px;text-align:center;font-size:12px">${k.monthly_volume||'-'}</td>
              <td style="padding:8px;text-align:center"><span style="color:${prioColor[k.priority]||'#3b82f6'};font-size:12px;font-weight:700">${k.priority}</span></td>
              <td style="padding:8px;text-align:center">
                <button class="btn btn-ghost" style="font-size:11px;padding:3px 8px" onclick="addKwSuggestion('${k.keyword.replace(/'/g,"\\'")}','${k.match_type}')">+ 除外KWに追加</button>
              </td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>
    `;
  } catch(e) { wrap.innerHTML = `<div class="card"><p style="color:var(--danger)">エラー: ${e.message}</p></div>`; }
}

window.addKwSuggestion = async function(keyword, matchType) {
  try {
    await api(`/negative-keywords/add`, {
      method: 'POST',
      body: JSON.stringify({ clinic_id: currentClinicId, keyword, match_type: matchType, source: 'ai_suggest' })
    });
    toast(`「${keyword}」を除外KWに追加しました`, 'success');
  } catch(e) { toast('追加失敗: '+e.message, 'error'); }
};

// ============================================================
// ---- Phase 2C: 競合分析 ----
// ============================================================
function loadCompetitor() {
  const btn = document.getElementById('competitorBtn');
  if (btn) btn.onclick = runCompetitor;
}

async function runCompetitor() {
  const area = document.getElementById('compArea')?.value?.trim();
  const service = document.getElementById('compService')?.value?.trim() || '整体院';
  if (!area) { toast('エリアを入力してください', 'error'); return; }
  const wrap = document.getElementById('competitorResults');
  wrap.innerHTML = '<div class="card"><p style="text-align:center;padding:24px;color:var(--text-2)">🕵️ AI分析中...</p></div>';
  try {
    const d = await api('/competitor-analysis', {
      method: 'POST',
      body: JSON.stringify({ clinic_id: currentClinicId, area, service_type: service })
    });
    if (!d.success) { wrap.innerHTML = `<div class="card"><p style="color:var(--danger)">${d.error}</p></div>`; return; }
    const a = d.analysis || {};
    wrap.innerHTML = `
      <div class="card">
        <div class="card-title">🕵️ ${area} × ${service} 競合分析結果</div>
        ${a.competitor_patterns?.length ? `
          <div style="margin-bottom:20px">
            <h4 style="font-size:13px;color:var(--text-2);margin-bottom:10px">⚔️ よく見られる競合パターン</h4>
            ${a.competitor_patterns.map(p => `
              <div style="margin-bottom:8px;padding:12px;background:#0f172a;border-radius:8px;border-left:3px solid #ef4444">
                <div style="font-size:13px;font-weight:600">${p.pattern}</div>
                <div style="font-size:12px;color:var(--text-3);margin:4px 0">例: ${p.example}</div>
                <div style="font-size:12px;color:#f59e0b">弱点: ${p.weakness}</div>
              </div>
            `).join('')}
          </div>
        ` : ''}
        ${a.differentiation_points?.length ? `
          <div style="margin-bottom:20px">
            <h4 style="font-size:13px;color:var(--text-2);margin-bottom:10px">✨ 差別化ポイント</h4>
            ${a.differentiation_points.map(p => `
              <div style="margin-bottom:8px;padding:12px;background:#0f172a;border-radius:8px;border-left:3px solid #10b981">
                <div style="font-size:13px;font-weight:600">💪 ${p.point}</div>
                <div style="font-size:12px;color:var(--text-2);margin-top:4px">→ ${p.how_to_use}</div>
              </div>
            `).join('')}
          </div>
        ` : ''}
        ${a.recommended_headlines?.length ? `
          <div style="margin-bottom:20px">
            <h4 style="font-size:13px;color:var(--text-2);margin-bottom:10px">📝 推奨見出し</h4>
            <div style="display:flex;flex-direction:column;gap:6px">
              ${a.recommended_headlines.map(h => `
                <div style="padding:10px 14px;background:#0f172a;border-radius:8px;border:1px solid var(--border);font-size:13px;font-weight:600">
                  ${h}
                </div>
              `).join('')}
            </div>
          </div>
        ` : ''}
        ${a.overall_strategy ? `
          <div style="padding:14px;background:rgba(59,130,246,0.1);border:1px solid rgba(59,130,246,0.3);border-radius:8px">
            <div style="font-size:12px;color:#60a5fa;font-weight:700;margin-bottom:6px">🎯 総合戦略提案</div>
            <div style="font-size:13px">${a.overall_strategy}</div>
          </div>
        ` : ''}
        ${a.recommended_descriptions?.length ? `
          <div style="margin-top:16px">
            <h4 style="font-size:13px;color:var(--text-2);margin-bottom:10px">📝 推奨説明文</h4>
            <div style="display:flex;flex-direction:column;gap:6px">
              ${a.recommended_descriptions.map(d => `
                <div style="padding:10px 14px;background:#0f172a;border-radius:8px;border:1px solid var(--border);font-size:12px;color:var(--text-2);line-height:1.6">
                  ${d}
                </div>
              `).join('')}
            </div>
          </div>
        ` : ''}
      </div>
    `;
  } catch(e) { wrap.innerHTML = `<div class="card"><p style="color:var(--danger)">エラー: ${e.message}</p></div>`; }
}

init();

// data-copy属性のクリックイベント委任
document.body.addEventListener('click', function(e) {
  const el = e.target.closest('[data-copy]');
  if(el) {
    const text = el.getAttribute('data-copy').replace(/&quot;/g, '"');
    const label = el.getAttribute('data-copy-label') || 'テキスト';
    if(typeof copyText === 'function') copyText(text, label);
  }
});

// ── 広告文コピー機能 ──────────────────────────
function copyText(text, label) {
  navigator.clipboard.writeText(text).then(() => {
    toast(`${label}をコピーしました`, 'success', 2000);
  }).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta);
    ta.select(); document.execCommand('copy');
    document.body.removeChild(ta);
    toast(`${label}をコピーしました`, 'success', 2000);
  });
}

window.copyAllAdCopy = function copyAllAdCopy() {
  const d = window._lastAdCopyData;
  if(!d) return;
  const text = [
    '【見出し】',
    ...d.headlines.map((h,i) => `H${i+1}: ${h}`),
    '',
    '【説明文】',
    ...d.descs.map((d,i) => `D${i+1}: ${d}`)
  ].join('\n');
  navigator.clipboard.writeText(text).then(() => {
    toast('広告文を全てコピーしました', 'success', 2500);
  });
}

// ── キャンペーンCSVエクスポート ──────────────────────────
window.exportCampaignsCSV = function exportCampaignsCSV() {
  // ダッシュボードまたはキャンペーンページのテーブルを対象
  const rows = document.querySelectorAll('#dashCampaignTable tr, #campaignList tr');
  if(!rows.length) { toast('データがありません', 'error'); return; }
  const csvLines = ['キャンペーン名,状態,表示回数,CTR(%),CVR(%),平均CPC(円),費用(円),CV数'];
  rows.forEach(r => {
    const cells = Array.from(r.querySelectorAll('td,th')).map(c => `"${c.innerText.replace(/"/g,'""')}"`);
    if(cells.length) csvLines.push(cells.join(','));
  });
  const blob = new Blob(['﻿' + csvLines.join('\n')], {type:'text/csv;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `admu_campaigns_${new Date().toISOString().slice(0,10)}.csv`;
  a.click(); URL.revokeObjectURL(url);
  toast('CSVをダウンロードしました', 'success');
}

// ── キーボードショートカット ──────────────────────────
document.addEventListener('keydown', (e) => {
  // input/textarea中は無視
  if(['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)) return;
  const shortcuts = { 'd':'dashboard','c':'campaigns','b':'budget','r':'bid-rules','a':'ad-copy','s':'settings' };
  if(e.key in shortcuts && !e.ctrlKey && !e.metaKey) {
    const link = document.querySelector(`[data-page="${shortcuts[e.key]}"]`);
    if(link) link.click();
  }
  if(e.key === '?') {
    toast('ショートカット: D=ダッシュボード C=キャンペーン B=予算 R=入札 A=AI広告文 S=設定', 'info', 5000);
  }
});
// ============================================================
// 管理者パネル
// ============================================================
let adminKpiPeriod = '7d'; // '7d' | '30d' | 'month' | 'this_year' | 'last_year' | 'custom'
let adminCustomStart = '';
let adminCustomEnd = '';

async function loadAdminPage() {
  const user = getUser();
  if (user.role !== 'admin') { toast('管理者権限が必要です', 'error'); return; }

  const el = document.getElementById('adminClinicTable');
  el.innerHTML = '<div class="loading-state"><div class="spinner"></div><span>読み込み中...</span></div>';
  try {
    const qs = (adminKpiPeriod === 'custom' && adminCustomStart && adminCustomEnd) 
      ? `?start=${adminCustomStart}&end=${adminCustomEnd}` : '';
    const data = await fetch(`${API}/admin/overview${qs}`, { headers: authHeaders() }).then(r => r.json());
    const clinics = data.clinics || [];
    if (!clinics.length) {
      el.innerHTML = '<p style="color:var(--text-3);padding:24px;text-align:center">クライアントがまだ登録されていません</p>';
      return;
    }

    // 期間タブ
    const tabHtml = `
      <div style="display:flex;gap:4px;background:rgba(255,255,255,0.05);border-radius:8px;padding:3px;border:1px solid rgba(255,255,255,0.07);margin-bottom:16px;width:fit-content;align-items:center;flex-wrap:wrap">
        <button id="adminTab7d"   class="range-btn ${adminKpiPeriod==='7d'    ? 'range-active':''}" onclick="switchAdminPeriod('7d',   this)">7日</button>
        <button id="adminTab30d"  class="range-btn ${adminKpiPeriod==='30d'   ? 'range-active':''}" onclick="switchAdminPeriod('30d',  this)">30日</button>
        <button id="adminTabMonth" class="range-btn ${adminKpiPeriod==='month' ? 'range-active':''}" onclick="switchAdminPeriod('month',this)">今月</button>
        <button id="adminTabThisYear" class="range-btn ${adminKpiPeriod==='this_year' ? 'range-active':''}" onclick="switchAdminPeriod('this_year',this)">今年</button>
        <button id="adminTabLastYear" class="range-btn ${adminKpiPeriod==='last_year' ? 'range-active':''}" onclick="switchAdminPeriod('last_year',this)">昨年</button>
        <button id="adminTabCustom" class="range-btn ${adminKpiPeriod==='custom' ? 'range-active':''}" onclick="switchAdminPeriod('custom',this)">🔧 カスタム</button>
        
        <div style="display:${adminKpiPeriod==='custom'?'flex':'none'};gap:4px;align-items:center;margin-left:8px;animation:fade-in 0.2s ease-out">
          <input type="date" id="adminCustomStart" class="form-input" style="font-size:11px;padding:2px 8px;height:26px;width:115px;background:var(--bg-card)" value="${adminCustomStart}">
          <span style="color:var(--text-3);font-size:12px">〜</span>
          <input type="date" id="adminCustomEnd" class="form-input" style="font-size:11px;padding:2px 8px;height:26px;width:115px;background:var(--bg-card)" value="${adminCustomEnd}">
          <button class="btn btn-primary" style="font-size:11px;padding:2px 10px;height:26px;min-height:26px" onclick="applyAdminCustomDate()">適用</button>
        </div>
      </div>`;

    const rows = clinics.map(c => {
      const kpi = c[`kpi_${adminKpiPeriod}`] || {};
      const costYen = Math.round((kpi.cost_micros || 0) / 1e6);
      const statusClass = c.status === 'active' ? 'enabled' : 'warning';
      const statusLabel = c.status === 'active' ? '🟢 有効中' : '🔴 停止中';
      return `
        <tr>
          <td style="color:var(--text-3);font-size:12px">#${c.clinic_id}</td>
          <td>
            <div style="font-weight:600">${c.clinic_name}</div>
            <div style="font-size:10px;color:var(--text-3)">📊 ${c.active_campaigns}本 🔧 ${c.active_bid_rules}ルール</div>
          </td>
          <td><span class="status-badge info" style="font-size:10px">${c.plan_name}</span></td>
          <td><span class="status-badge ${statusClass}" style="font-size:10px">${statusLabel}</span></td>
          <td style="text-align:right;font-weight:700;color:${costYen > 100000 ? 'var(--yellow)' : 'var(--text-1)'}">
            ¥${costYen.toLocaleString()}
          </td>
          <td style="text-align:right">${(kpi.clicks||0).toLocaleString()}</td>
          <td style="text-align:right">${(kpi.impressions||0).toLocaleString()}</td>
          <td style="text-align:right;color:${(kpi.ctr||0)>=3?'var(--green)':'var(--text-2)'}">${kpi.ctr||0}%</td>
          <td style="text-align:right;color:${(kpi.cvr||0)>=5?'var(--green)':'var(--text-2)'}">${kpi.cvr||0}%</td>
          <td style="text-align:right">${kpi.conversions||0}</td>
          <td style="text-align:right;color:var(--text-2)">¥${(kpi.cpc_yen||0).toLocaleString()}</td>
          <td style="text-align:right;color:var(--text-2)">
            ${kpi.cpa_yen ? '¥'+kpi.cpa_yen.toLocaleString() : '-'}
          </td>
          <td>
            <div style="display:flex;gap:4px;flex-wrap:wrap">
              <button class="btn btn-ghost" style="font-size:10px;padding:2px 7px"
                onclick="adminShowDetail(${c.clinic_id},'${c.clinic_name}')">🔍 詳細</button>
              <button class="btn btn-ghost" style="font-size:10px;padding:2px 7px"
                onclick="adminSetLimit(${c.clinic_id},'${c.clinic_name}')">⚖ 上限</button>
              <button class="btn btn-ghost" style="font-size:10px;padding:2px 7px;color:${c.status==='active'?'var(--red)':'var(--green)'}"
                onclick="adminToggleStatus(${c.clinic_id},'${c.status}')">
                ${c.status==='active'?'⏸ 停止する':'▶ 有効にする'}
              </button>
              <button class="btn btn-ghost" style="font-size:10px;padding:2px 7px"
                onclick="adminArchiveAdsSetting(${c.clinic_id},'${c.clinic_name}')">📦 保管</button>
            </div>
          </td>
        </tr>`;
    }).join('');

    el.innerHTML = tabHtml + `
      <div style="overflow-x:auto">
        <table class="data-table" style="min-width:900px">
          <thead>
            <tr>
              <th>ID</th><th>クリニック名</th><th>プラン</th><th>状態</th>
              <th style="text-align:right">費用</th>
              <th style="text-align:right">クリック</th>
              <th style="text-align:right">表示回数</th>
              <th style="text-align:right">CTR</th>
              <th style="text-align:right">CVR</th>
              <th style="text-align:right">CV数</th>
              <th style="text-align:right">CPC</th>
              <th style="text-align:right">CPA</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  } catch(e) {
    el.innerHTML = `<p style="color:var(--red);padding:24px;text-align:center">読み込み失敗: ${e.message}</p>`;
  }
}

window.switchAdminPeriod = function(period, btn) {
  adminKpiPeriod = period;
  if(period !== 'custom') {
    loadAdminPage();
  } else {
    // UIを再描画してカスタム日付ピッカーを表示
    loadAdminPage();
  }
};

window.applyAdminCustomDate = function() {
  const start = document.getElementById('adminCustomStart').value;
  const end = document.getElementById('adminCustomEnd').value;
  if(!start || !end) {
    toast('開始日と終了日の両方を選択してください', 'error');
    return;
  }
  adminCustomStart = start;
  adminCustomEnd = end;
  loadAdminPage();
};

// ===== タブ切り替え =====
let adminCurrentTab = 'kpi';
window.switchAdminTab = function(tab) {
  adminCurrentTab = tab;
  ['kpi', 'contracts', 'add', 'announce', 'inquiry'].forEach(t => {
    const pane = document.getElementById(`adminTabPane-${t}`);
    const btn  = document.getElementById(`adminTab${t.charAt(0).toUpperCase() + t.slice(1)}`);
    if (pane) pane.style.display = t === tab ? '' : 'none';
    if (btn)  { btn.classList.toggle('range-active', t === tab); }
  });
  if (tab === 'contracts') loadAdminContracts();
  if (tab === 'add')       populateClinicSelect('contractClinicId');
  if (tab === 'announce')  loadAdminAnnouncements();
  if (tab === 'inquiry')   loadInquiries();
};

// ============================================================
// お知らせ管理
// ============================================================
async function loadAnnouncements() {
  try {
    const d = await api('/announcements?limit=3');
    const list = document.getElementById('announcementList');
    const container = document.getElementById('announcementContainer');
    if (!d.announcements || d.announcements.length === 0) {
      if(container) container.style.display = 'none';
      return;
    }
    if(container) container.style.display = 'block';
    list.innerHTML = d.announcements.map(a => `
      <div style="font-size:13px"><span style="color:var(--text-3);margin-right:8px">${fmtDate(a.published_at).split(' ')[0]}</span> <span style="font-weight:600;color:var(--text-1)">${a.title}</span></div>
      <div style="font-size:12px;color:var(--text-2);margin-left:85px;margin-top:2px;white-space:pre-wrap">${a.content}</div>
    `).join('<div style="border-bottom:1px dashed rgba(255,255,255,0.1);margin:4px 0"></div>');
  } catch(e) { console.error('お知らせ取得エラー', e); }
}

async function loadAdminAnnouncements() {
  try {
    const wrap = document.getElementById('adminAnnouncementList');
    wrap.innerHTML = '<div style="color:var(--text-3);font-size:12px">読み込み中...</div>';
    const d = await api('/announcements?limit=20');
    if (!d.announcements || d.announcements.length === 0) {
      wrap.innerHTML = '<div style="color:var(--text-3);font-size:12px">お知らせはありません</div>';
      return;
    }
    wrap.innerHTML = `
      <table style="width:100%;border-collapse:collapse;font-size:12px">
        <thead><tr style="border-bottom:1px solid rgba(255,255,255,0.1);color:var(--text-3);text-align:left"><th style="padding:8px">配信日時</th><th style="padding:8px">タイトル</th><th style="padding:8px;text-align:right">アクション</th></tr></thead>
        <tbody>
          ${d.announcements.map(a => `
            <tr style="border-bottom:1px solid rgba(255,255,255,0.05)">
              <td style="padding:8px;width:120px">${fmtDate(a.published_at)}</td>
              <td style="padding:8px;font-weight:600">${a.title}</td>
              <td style="padding:8px;text-align:right"><button class="btn btn-danger" style="font-size:10px;padding:2px 6px" onclick="adminDelAnnouncement(${a.id})">削除</button></td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  } catch(e) { toast('お知らせ取得失敗', 'error'); }
}

window.adminPostAnnouncement = async function() {
  const title = document.getElementById('adminAnnounceTitle').value.trim();
  const content = document.getElementById('adminAnnounceContent').value.trim();
  if(!title || !content) { toast('タイトルと内容を入力してください', 'error'); return; }
  try {
    const res = await fetch(`${API}/announcements`, { method: 'POST', headers: {'Content-Type': 'application/json', ...authHeaders()}, body: JSON.stringify({title, content}) }).then(r => r.json());
    if(res.success) {
      toast('お知らせを配信しました', 'success');
      document.getElementById('adminAnnounceTitle').value = '';
      document.getElementById('adminAnnounceContent').value = '';
      loadAdminAnnouncements();
    } else throw new Error(res.error||'失敗');
  } catch(e) { toast(e.message, 'error'); }
};

window.adminDelAnnouncement = async function(id) {
  if(!confirm('削除しますか？')) return;
  try {
    await fetch(`${API}/announcements/${id}`, { method: 'DELETE', headers: authHeaders() });
    toast('お知らせを削除しました', 'success');
    loadAdminAnnouncements();
  } catch(e) { toast(e.message, 'error'); }
};

// ===== 契約一覧ロード =====
async function loadAdminContracts() {
  const el = document.getElementById('adminContractList');
  if (!el) return;
  el.innerHTML = '<div class="loading-state"><div class="spinner"></div></div>';
  try {
    const data = await fetch(`${API}/admin/contracts`, { headers: authHeaders() }).then(r => r.json());
    const contracts = data.contracts || [];
    if (!contracts.length) {
      el.innerHTML = '<p style="color:var(--text-3);padding:20px;text-align:center">契約データがありません</p>';
      return;
    }
    const rows = contracts.map(c => {
      const statusMap = { active:'有効', suspended:'停止', cancelled:'解約', inactive:'未契約' };
      const colorMap  = { active:'enabled', suspended:'warning', cancelled:'error', inactive:'paused' };
      const st = c.status || 'inactive';
      const renewDays = c.renewal_at
        ? Math.ceil((new Date(c.renewal_at) - new Date()) / 86400000)
        : null;
      const renewColor = renewDays !== null && renewDays <= 14 ? 'var(--red)' : 'var(--text-2)';
      return `
        <tr style="cursor:pointer" onclick="adminFillContractForm(${c.clinic_id},'${c.clinic_name}','${c.plan_name||''}',${c.monthly_fee||0},'${st}','${c.started_at||''}','${c.renewal_at||''}','${(c.notes||'').replace(/'/g,"&#39;")}')">
          <td>#${c.clinic_id}</td>
          <td style="font-weight:600">${c.clinic_name}</td>
          <td><span class="status-badge info" style="font-size:10px">${c.plan_name||'未設定'}</span></td>
          <td><span class="status-badge ${colorMap[st]||'paused'}" style="font-size:10px">${statusMap[st]||st}</span></td>
          <td style="text-align:right;font-weight:700">
            ${c.monthly_fee ? '¥'+Number(c.monthly_fee).toLocaleString() : '-'}
          </td>
          <td style="text-align:right;font-size:12px">${c.started_at||'-'}</td>
          <td style="text-align:right;font-size:12px;color:${renewColor}">
            ${c.renewal_at ? c.renewal_at + (renewDays!==null ? ` (${renewDays}日後)` : '') : '-'}
          </td>
          <td>
            <button class="btn btn-ghost" style="font-size:10px;padding:2px 8px;color:var(--red)"
              onclick="event.stopPropagation();adminCancelContractById(${c.clinic_id},'${c.clinic_name}')">
              🚫 解除
            </button>
          </td>
        </tr>`;
    }).join('');

    el.innerHTML = `
      <div style="overflow-x:auto">
        <table class="data-table" style="min-width:700px">
          <thead>
            <tr>
              <th>ID</th><th>クリニック名</th><th>プラン</th><th>状態</th>
              <th style="text-align:right">月額</th>
              <th style="text-align:right">契約開始</th>
              <th style="text-align:right">次回更新</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <p style="font-size:11px;color:var(--text-3);margin-top:8px">行をクリックで下のフォームに読み込みます</p>`;
    // クリニック選択プルダウンも更新
    populateClinicSelect('contractClinicId', contracts);
  } catch(e) {
    el.innerHTML = `<p style="color:var(--red);padding:20px">取得失敗: ${e.message}</p>`;
  }
}

// フォームに契約情報をセット（行クリック時）
window.adminFillContractForm = function(cid, name, plan, fee, status, started, renewal, notes) {
  document.getElementById('contractClinicId').value = cid;
  document.getElementById('contractPlan').value = plan || 'スタンダード';
  document.getElementById('contractFee').value = fee || 49800;
  document.getElementById('contractStatus').value = status || 'active';
  document.getElementById('contractStarted').value = started || '';
  document.getElementById('contractRenewal').value = renewal || '';
  document.getElementById('contractNotes').value = notes ? notes.replace(/&#39;/g,"'") : '';
};

// クリニック選択肢のプルダウン更新
async function populateClinicSelect(selId, contractsData) {
  const sel = document.getElementById(selId);
  if (!sel) return;
  let rows = contractsData;
  if (!rows) {
    const data = await fetch(`${API}/admin/contracts`, { headers: authHeaders() }).then(r => r.json());
    rows = data.contracts || [];
  }
  const seen = new Set();
  sel.innerHTML = rows
    .filter(c => { if (seen.has(c.clinic_id)) return false; seen.add(c.clinic_id); return true; })
    .map(c => `<option value="${c.clinic_id}">${c.clinic_name} (#${c.clinic_id})</option>`)
    .join('');
}

// 契約保存
window.adminSaveContract = async function() {
  const cid    = parseInt(document.getElementById('contractClinicId')?.value || '0');
  const plan   = document.getElementById('contractPlan')?.value || 'スタンダード';
  const fee    = parseInt(document.getElementById('contractFee')?.value || '0');
  const status = document.getElementById('contractStatus')?.value || 'active';
  const started = document.getElementById('contractStarted')?.value || null;
  const renewal = document.getElementById('contractRenewal')?.value || null;
  const notes  = document.getElementById('contractNotes')?.value || '';
  if (!cid) { toast('クリニックを選択してください', 'error'); return; }
  try {
    const res = await fetch(`${API}/admin/contracts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ clinic_id: cid, plan_name: plan, monthly_fee: fee,
                             status, started_at: started, renewal_at: renewal, notes })
    }).then(r => r.json());
    if (res.success) { toast('契約を保存しました', 'success'); loadAdminContracts(); }
    else toast('保存失敗', 'error');
  } catch(e) { toast('保存失敗: ' + e.message, 'error'); }
};

// 契約解除（フォームから）
window.adminCancelContract = function() {
  const cid = parseInt(document.getElementById('contractClinicId')?.value || '0');
  if (!cid) { toast('クリニックを選択してください', 'error'); return; }
  adminCancelContractById(cid, '選択中のクリニック');
};

// 契約解除（行の解除ボタン or フォーム）
window.adminCancelContractById = async function(clinicId, clinicName) {
  if (!confirm(`「${clinicName}」の契約を解除しますか？\nステータスが「解約」に変わります。`)) return;
  try {
    const res = await fetch(`${API}/admin/contracts/${clinicId}`, {
      method: 'DELETE', headers: authHeaders()
    }).then(r => r.json());
    if (res.success) { toast(res.message || '契約を解除しました', 'success'); loadAdminContracts(); }
    else toast('解除失敗', 'error');
  } catch(e) { toast('解除失敗: ' + e.message, 'error'); }
};


// 詳細モーダル（キャンペーン別数値）
window.adminShowDetail = async function(clinicId, clinicName) {
  showModal(`📊 ${clinicName} の詳細`, '<div class="loading-state"><div class="spinner"></div></div>');
  try {
    const pw = document.getElementById('adminPwInput')?.value || '';
    const data = await fetch(`${API}/admin/clinics/${clinicId}/data?password=${encodeURIComponent(pw)}`, {
      headers: authHeaders()
    }).then(r => r.json());

    const acc = data.account || {};
    const campaigns = data.campaigns || [];
    const adCopies = data.ad_copies || [];

    const campRows = campaigns.map(c => `
      <tr>
        <td style="font-size:12px;font-weight:600">${c.name}</td>
        <td><span class="status-badge ${c.status==='ENABLED'?'enabled':'paused'}" style="font-size:10px">${c.status==='ENABLED'?'配信中':'停止'}</span></td>
        <td style="text-align:right">¥${Math.round((c.budget_micros||0)/1e6).toLocaleString()}</td>
        <td style="text-align:right">${(c.impressions||0).toLocaleString()}</td>
        <td style="text-align:right">${c.ctr||0}%</td>
        <td style="text-align:right">${c.cvr||0}%</td>
        <td style="text-align:right">${c.conversions||0}</td>
      </tr>`).join('') || '<tr><td colspan="7" style="text-align:center;color:var(--text-3);padding:20px">データなし</td></tr>';

    const body = `
      <div style="margin-bottom:16px">
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:12px">
          <div style="background:var(--bg-card2);padding:10px;border-radius:8px;text-align:center">
            <div style="font-size:10px;color:var(--text-3)">アクティブキャンペーン</div>
            <div style="font-size:20px;font-weight:800">${campaigns.filter(c=>c.status==='ENABLED').length}</div>
          </div>
          <div style="background:var(--bg-card2);padding:10px;border-radius:8px;text-align:center">
            <div style="font-size:10px;color:var(--text-3)">広告文数</div>
            <div style="font-size:20px;font-weight:800">${adCopies.length}</div>
          </div>
          <div style="background:var(--bg-card2);padding:10px;border-radius:8px;text-align:center">
            <div style="font-size:10px;color:var(--text-3)">モックモード</div>
            <div style="font-size:20px;font-weight:800">${acc.mock_mode?'ON':'OFF'}</div>
          </div>
        </div>
        <h4 style="font-size:12px;color:var(--text-3);margin-bottom:8px;text-transform:uppercase;letter-spacing:0.5px">キャンペーン一覧</h4>
        <div style="overflow-x:auto">
          <table class="data-table" style="min-width:500px">
            <thead>
              <tr>
                <th>キャンペーン名</th><th>状態</th><th style="text-align:right">予算</th>
                <th style="text-align:right">表示</th><th style="text-align:right">CTR</th>
                <th style="text-align:right">CVR</th><th style="text-align:right">CV</th>
              </tr>
            </thead>
            <tbody>${campRows}</tbody>
          </table>
        </div>
      </div>`;
    document.getElementById('modalBody').innerHTML = body;
  } catch(e) {
    document.getElementById('modalBody').innerHTML = `<p style="color:var(--red)">取得失敗: ${e.message}</p>`;
  }
};


// クライアント追加
window.adminAddClinic = async function() {
  const name = document.getElementById('adminNewClinicName')?.value?.trim();
  const maxAcc = parseInt(document.getElementById('adminNewClinicMaxAccounts')?.value || '1');
  if (!name) { toast('クリニック名を入力してください', 'error'); return; }
  try {
    const res = await fetch(`${API}/admin/clinics`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ name, max_sub_accounts: maxAcc })
    }).then(r => r.json());
    if (res.success) {
      toast(`「${name}」を追加しました (ID: ${res.clinic_id})`, 'success');
      document.getElementById('adminNewClinicName').value = '';
      loadAdminPage();
    }
  } catch(e) { toast('追加失敗: ' + e.message, 'error'); }
};

// サブアカウント上限変更
window.adminSetLimit = function(clinicId, clinicName) {
  const body = `
    <div style="margin-bottom:12px;font-size:14px;">「${clinicName}」のサブアカウント上限（-1=無制限, 1=追加不可, 2以上=N件まで）</div>
    <input type="number" id="modalLimitInput" class="form-input" value="-1" min="-1" style="width:100%">
  `;
  const footer = `
    <button class="btn btn-ghost" onclick="closeModal()">キャンセル</button>
    <button class="btn btn-primary" id="modalLimitBtn">設定を保存</button>
  `;
  showModal('⚖ サブアカウント上限設定', body, footer);
  
  document.getElementById('modalLimitBtn').onclick = () => {
    const val = parseInt(document.getElementById('modalLimitInput').value);
    if (isNaN(val) || val < -1) { toast('有効な数値を入力してください', 'error'); return; }
    closeModal();
    fetch(`${API}/admin/clinics/set-limit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ clinic_id: clinicId, max_sub_accounts: val })
    }).then(r => r.json()).then(d => {
      if (d.success) { toast(d.message, 'success'); loadAdminPage(); }
    }).catch(e => toast('変更失敗: ' + e.message, 'error'));
  };
};

// プランステータス切り替え
window.adminToggleStatus = function(clinicId, currentStatus) {
  const newStatus = currentStatus === 'active' ? 'suspended' : 'active';
  const label = newStatus === 'active' ? '再有効化（アクティブ化）' : '停止（サスペンド）';
  
  const body = `<div style="font-size:14px;padding:12px 0;">このクライアントを「<strong>${label}</strong>」に変更しますか？</div>`;
  const footer = `
    <button class="btn btn-ghost" onclick="closeModal()">キャンセル</button>
    <button class="btn btn-${newStatus === 'active' ? 'success' : 'danger'}" id="modalToggleBtn">はい、変更します</button>
  `;
  showModal('⚠️ ステータスの変更', body, footer);
  
  document.getElementById('modalToggleBtn').onclick = () => {
    closeModal();
    fetch(`${API}/admin/clinics`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ id: clinicId, plan_status: newStatus })
    }).then(r => r.json()).then(d => {
      if (d.success) { toast(`${label}しました`, 'success'); loadAdminPage(); }
    }).catch(e => toast('変更失敗: ' + e.message, 'error'));
  };
};

// 広告詳細データアーカイブ（保管）
window.adminArchiveAdsSetting = function(clinicId, clinicName) {
  const defaultNote = `${new Date().toLocaleDateString()}の構成`;
  const body = `
    <div style="font-size:13px;color:var(--text-2);margin-bottom:12px;">
      「${clinicName}」の広告構成（キャンペーン・KW・広告文など）のスナップショットをデータベースに保管し、今後の多店舗展開の資産データとして残します。
    </div>
    <div class="form-group">
      <label>保管用メモ（任意）</label>
      <input type="text" id="modalArchiveNote" class="form-input" value="${defaultNote}" style="width:100%">
    </div>
  `;
  const footer = `
    <button class="btn btn-ghost" onclick="closeModal()">キャンセル</button>
    <button class="btn btn-primary" id="modalArchiveBtn">📦 スナップショットを保管</button>
  `;
  showModal('📦 広告構成アーカイブ', body, footer);
  
  document.getElementById('modalArchiveBtn').onclick = () => {
    const notes = document.getElementById('modalArchiveNote').value || '';
    closeModal();
    toast('広告データを取得中...（DBへ保存）', 'info');
    fetch(`${API}/admin/clinics/${clinicId}/archive-ads`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ notes: notes })
    }).then(r => r.json()).then(d => {
      if(d.success) {
        toast(`アーカイブ保管完了！ (ID: ${d.archive_id})`, 'success');
      } else {
        toast('保管失敗: ' + (d.error || '不明なエラー'), 'error');
      }
    }).catch(e => toast('通信エラー: ' + e.message, 'error'));
  };
};


// ============================================================
// ★ WORLD-CLASS FEATURE ①: 心理トリガースコアリング
// Cialdini 9軸AI採点 + レーダーバー表示
// ============================================================
const _PSYCH_LABELS = {
  urgency:             { label: '緊急性',   color: '#ef4444' },
  scarcity:            { label: '希少性',   color: '#f97316' },
  social_proof:        { label: '社会的証明', color: '#eab308' },
  authority:           { label: '権威性',   color: '#22c55e' },
  specificity:         { label: '具体性',   color: '#06b6d4' },
  empathy:             { label: '共感性',   color: '#8b5cf6' },
  cta_clarity:         { label: 'CTA明確性', color: '#ec4899' },
  local_relevance:     { label: '地域密着', color: '#14b8a6' },
  symptom_specificity: { label: '症状特異性', color: '#f59e0b' },
};

async function runPsychScore(headlines, descriptions) {
  const panel = document.getElementById('psychScorePanel');
  const bars  = document.getElementById('psychScoreBars');
  const badge = document.getElementById('psychGradeBadge');
  const total = document.getElementById('psychTotalScore');
  const impDiv= document.getElementById('psychTopImprovement');
  const impTxt= document.getElementById('psychTopImprovementText');
  if (!panel) return;

  panel.style.display = 'block';
  bars.innerHTML = '<div style="text-align:center;padding:12px;color:var(--text-3);font-size:12px">🧠 心理トリガーを分析中...</div>';

  try {
    const d = await api('/ad-copy/psych-score', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clinic_id: currentClinicId, headlines, descriptions }),
    });
    if (!d.success || !d.result) { bars.innerHTML = '<div style="color:var(--text-3);font-size:12px">スコアリング失敗</div>'; return; }

    const r = d.result;
    const scores = r.scores || {};
    const gradeColors = { S:'#f59e0b', A:'#10b981', B:'#3b82f6', C:'#8b5cf6', D:'#ef4444' };

    // Gradeバッジ
    const grade = r.grade || 'B';
    badge.textContent = `Grade ${grade}`;
    badge.className = `grade-${grade}`;
    total.textContent = `${r.total_score || 0} / 90`;
    total.style.color = gradeColors[grade] || '#c8a97a';

    // スコアバー
    bars.innerHTML = Object.entries(_PSYCH_LABELS).map(([key, meta]) => {
      const score = scores[key] ?? 0;
      const pct   = Math.round(score / 10 * 100);
      return `
        <div class="psych-bar-row">
          <span style="color:var(--text-2)">${meta.label}</span>
          <div class="psych-bar-track">
            <div class="psych-bar-fill" style="width:${pct}%;background:${meta.color}"></div>
          </div>
          <span style="color:${meta.color};font-weight:700">${score}</span>
        </div>`;
    }).join('');

    // TOP改善提案
    if (r.top_improvement) {
      impTxt.textContent = r.top_improvement;
      impDiv.style.display = 'block';
    }
    toast(`心理スコア: ${r.total_score}/90 (Grade ${grade}) ✅`, 'success', 3000);
  } catch(e) {
    bars.innerHTML = `<div style="color:var(--danger);font-size:12px">エラー: ${e.message}</div>`;
  }
}


// ============================================================
// ★ WORLD-CLASS FEATURE ②: AI意思決定エンジン「今日やること」
// ============================================================
let _briefCache = null;

async function loadDailyBrief(forceRefresh = false) {
  const content = document.getElementById('dailyBriefContent');
  const genAt   = document.getElementById('briefGeneratedAt');
  if (!content) return;

  // キャッシュ利用（ページ移動しても再取得しない）
  if (_briefCache && !forceRefresh) {
    renderDailyBrief(_briefCache);
    return;
  }

  content.innerHTML = `<div style="display:flex;align-items:center;gap:10px;padding:20px;color:var(--text-3);justify-content:center"><div class="spinner" style="width:16px;height:16px"></div><span>AIが全KPIを分析中...</span></div>`;

  try {
    const d = await api(`/intelligence/daily-brief?clinic_id=${currentClinicId}`, { method: 'POST' });
    if (!d.success || !d.brief) throw new Error(d.error || '取得失敗');
    _briefCache = d;
    renderDailyBrief(d);
    if (genAt) genAt.textContent = `AI生成: ${d.brief.generated_at}`;
  } catch(e) {
    content.innerHTML = `<div style="color:var(--danger);font-size:12px;padding:12px">⚠️ ${e.message}（GEMINI_API_KEY未設定の可能性）</div>`;
  }
}

function renderDailyBrief(d) {
  const content = document.getElementById('dailyBriefContent');
  const genAt   = document.getElementById('briefGeneratedAt');
  if (!content || !d.brief) return;

  const b = d.brief;
  const healthColors = { good: '#10b981', warning: '#f59e0b', critical: '#ef4444' };
  const healthLabels = { good: '✅ 良好', warning: '⚠️ 要注意', critical: '🚨 緊急対応' };
  const healthColor  = healthColors[b.overall_health] || '#c8a97a';

  const urgencyColors = { 緊急: '#ef4444', 重要: '#f59e0b', 推奨: '#10b981' };
  const urgencyEmoji  = { 緊急: '🔴', 重要: '🟡', 推奨: '🟢' };

  content.innerHTML = `
    <!-- ヘルスサマリー -->
    <div style="display:flex;align-items:center;gap:16px;padding:12px 16px;background:rgba(0,0,0,0.2);border-radius:10px;margin-bottom:16px;flex-wrap:wrap">
      <div>
        <div style="font-size:10px;color:var(--text-3);letter-spacing:1px;text-transform:uppercase">広告健全度</div>
        <div style="font-size:22px;font-weight:900;color:${healthColor}">${b.health_score ?? '--'}<span style="font-size:12px;color:var(--text-3)">/100</span></div>
      </div>
      <div style="flex:1;min-width:120px">
        <div style="font-size:11px;color:${healthColor};font-weight:700;margin-bottom:4px">${healthLabels[b.overall_health] || '分析中'}</div>
        <div style="font-size:12px;color:var(--text-2);line-height:1.5">${b.situation || ''}</div>
      </div>
    </div>
    ${b.complication ? `
    <div style="padding:8px 12px;background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.2);border-radius:8px;font-size:12px;color:#fca5a5;margin-bottom:14px;line-height:1.5">
      ⚡ ${b.complication}
    </div>` : ''}
    <!-- 今日やること Top3 -->
    <div style="font-size:11px;color:var(--text-3);letter-spacing:1px;text-transform:uppercase;margin-bottom:8px">今日やること Top3</div>
    ${(b.actions || []).map(a => `
      <div class="brief-action-card">
        <div class="brief-priority-badge" style="background:${urgencyColors[a.urgency] || '#c8a97a'}22;color:${urgencyColors[a.urgency] || '#c8a97a'}">
          ${urgencyEmoji[a.urgency] || '⭐'}
        </div>
        <div style="flex:1;min-width:0">
          <div style="font-weight:700;font-size:13px;margin-bottom:4px">${a.action}</div>
          <div style="font-size:11px;color:var(--text-3);margin-bottom:4px">${a.reason}</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <span style="font-size:10px;background:rgba(16,185,129,0.15);color:#10b981;padding:2px 8px;border-radius:99px">📈 ${a.expected_impact}</span>
            <span style="font-size:10px;color:var(--text-3)">${a.how_to}</span>
          </div>
        </div>
      </div>
    `).join('')}
  `;
  if (genAt) genAt.textContent = `AI生成: ${b.generated_at}`;
}


// ============================================================
// ★ WORLD-CLASS FEATURE ③: LTVシミュレーター
// ============================================================
function updateSimPreview() {
  const unitPrice  = +document.getElementById('simUnitPrice')?.value  || 8000;
  const visitCount = +document.getElementById('simVisitCount')?.value || 8;
  const repeatRate = +document.getElementById('simRepeatRate')?.value || 35;
  const targetCv   = +document.getElementById('simTargetCv')?.value   || 15;
  const margin     = +document.getElementById('simMargin')?.value     || 30;
  const budget     = +document.getElementById('simBudget')?.value     || 100000;

  // ラベル更新
  if(document.getElementById('simUnitPriceVal'))  document.getElementById('simUnitPriceVal').textContent  = `¥${unitPrice.toLocaleString()}`;
  if(document.getElementById('simVisitCountVal')) document.getElementById('simVisitCountVal').textContent = `${visitCount}回`;
  if(document.getElementById('simRepeatRateVal')) document.getElementById('simRepeatRateVal').textContent = `${repeatRate}%`;
  if(document.getElementById('simTargetCvVal'))   document.getElementById('simTargetCvVal').textContent   = `${targetCv}件`;
  if(document.getElementById('simMarginVal'))     document.getElementById('simMarginVal').textContent     = `${margin}%`;
  if(document.getElementById('simBudgetVal'))     document.getElementById('simBudgetVal').textContent     = `¥${budget.toLocaleString()}`;

  // リアルタイム概算プレビュー（APIなし）
  const ltv         = Math.round(unitPrice * visitCount * (1 + repeatRate / 100 * 2));
  const optimalCpa  = Math.round(ltv * margin / 100);
  const reqBudget   = optimalCpa * targetCv;
  const curCv       = budget > 0 ? Math.round(budget / optimalCpa) : 0;
  const roi         = reqBudget > 0 ? Math.round((ltv * targetCv - reqBudget) / reqBudget * 100) : 0;

  const prev = document.getElementById('simPreviewResult');
  if (!prev) return;
  prev.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
      ${[
        { label:'患者LTV（推定）', val:`¥${ltv.toLocaleString()}`, color:'#c8a97a', desc:'生涯価値' },
        { label:'適正CPA上限',     val:`¥${optimalCpa.toLocaleString()}`, color:'#10b981', desc:'1新患あたり' },
        { label:'必要月間予算',    val:`¥${reqBudget.toLocaleString()}`, color:'#3b82f6', desc:`目標${targetCv}件達成` },
        { label:'現在の推定CV数',  val:`${curCv}件/月`, color:'#8b5cf6', desc:`予算¥${(budget/10000).toFixed(0)}万から` },
      ].map(k => `
        <div style="background:#0f172a;border-radius:10px;padding:14px;border:1px solid rgba(255,255,255,0.06)">
          <div style="font-size:10px;color:var(--text-3);margin-bottom:4px">${k.label}</div>
          <div style="font-size:20px;font-weight:900;color:${k.color}">${k.val}</div>
          <div style="font-size:10px;color:var(--text-3)">${k.desc}</div>
        </div>`).join('')}
    </div>
    <div style="display:flex;align-items:center;gap:12px;padding:12px 16px;background:${roi>=0?'rgba(16,185,129,0.1)':'rgba(239,68,68,0.1)'};border:1px solid ${roi>=0?'rgba(16,185,129,0.3)':'rgba(239,68,68,0.3)'};border-radius:10px">
      <span style="font-size:28px;font-weight:900;color:${roi>=0?'#10b981':'#ef4444'}">${roi>=0?'+':''}${roi}%</span>
      <div>
        <div style="font-size:12px;font-weight:700">目標シナリオROI</div>
        <div style="font-size:11px;color:var(--text-3)">AI解釈付き詳細は「計算する」ボタンで取得</div>
      </div>
    </div>`;
}

async function runLtvSimulator() {
  const btn = document.getElementById('simRunBtn');
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '⏳ AI解釈中...';

  const payload = {
    clinic_id:       currentClinicId,
    avg_unit_price:  +document.getElementById('simUnitPrice')?.value  || 8000,
    avg_visit_count: +document.getElementById('simVisitCount')?.value || 8,
    repeat_rate:     (+document.getElementById('simRepeatRate')?.value || 35) / 100,
    target_monthly_cv: +document.getElementById('simTargetCv')?.value || 15,
    profit_margin:   (+document.getElementById('simMargin')?.value    || 30) / 100,
    current_budget:  +document.getElementById('simBudget')?.value     || 100000,
  };

  try {
    const d = await api('/simulator/ltv', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!d.success) throw new Error(d.error);

    // シナリオテーブル
    const scenEl = document.getElementById('simScenarios');
    if (scenEl) {
      scenEl.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:8px">
          ${d.scenarios.map(s => `
            <div style="display:flex;align-items:center;justify-content:space-between;padding:12px 14px;background:#0f172a;border-radius:8px;border:1px solid rgba(255,255,255,0.06);gap:8px;flex-wrap:wrap">
              <div>
                <span style="font-size:11px;font-weight:700;background:rgba(200,169,122,0.2);color:#c8a97a;padding:2px 8px;border-radius:99px">${s.label}</span>
                <span style="font-size:11px;color:var(--text-3);margin-left:6px">${s.recommendation}</span>
              </div>
              <div style="display:flex;gap:16px;align-items:center">
                <div style="text-align:center"><div style="font-size:10px;color:var(--text-3)">予算</div><div style="font-size:14px;font-weight:700">¥${s.budget.toLocaleString()}</div></div>
                <div style="text-align:center"><div style="font-size:10px;color:var(--text-3)">新患</div><div style="font-size:14px;font-weight:700;color:#06b6d4">${s.expected_cv}件</div></div>
                <div style="text-align:center"><div style="font-size:10px;color:var(--text-3)">ROI</div><div style="font-size:14px;font-weight:700;color:${s.expected_roi>=0?'#10b981':'#ef4444'}">${s.expected_roi>=0?'+':''}${s.expected_roi}%</div></div>
              </div>
            </div>
          `).join('')}
        </div>`;
    }

    // AIインサイト
    const insightEl = document.getElementById('simAiInsight');
    const insightCard = document.getElementById('simInsightCard');
    if (insightEl && d.ai_insight) {
      insightEl.textContent = d.ai_insight;
      if (insightCard) insightCard.style.display = 'block';
    }

    toast(`LTV計算完了 ✅ 適正CPA: ¥${d.optimal_cpa.toLocaleString()} / ROI: +${d.roi}%`, 'success', 4000);
    updateSimPreview();
  } catch(e) {
    toast('計算失敗: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
  }
}


// ============================================================
// ★ WORLD-CLASS FEATURE ④: ナラティブAIレポート (SCQA)
// ============================================================
let _narrativeCache = null;

async function loadNarrativeReport(forceRefresh = false) {
  const content = document.getElementById('narrativeContent');
  const genAt   = document.getElementById('narrativeGeneratedAt');
  if (!content) return;

  if (_narrativeCache && !forceRefresh) {
    renderNarrative(_narrativeCache);
    return;
  }

  content.innerHTML = `<div style="display:flex;align-items:center;gap:10px;padding:20px;color:var(--text-3);justify-content:center"><div class="spinner" style="width:16px;height:16px"></div><span>McKinsey SCQAフレームワークで分析中...</span></div>`;

  try {
    const d = await api(`/narrative-report?clinic_id=${currentClinicId}&days=7`);
    if (!d.success || !d.narrative) throw new Error(d.error || '取得失敗');
    _narrativeCache = d;
    renderNarrative(d);
    if (genAt) genAt.textContent = `AI生成: ${d.generated_at}`;
  } catch(e) {
    content.innerHTML = `<div style="color:var(--danger);font-size:12px;padding:12px">⚠️ ${e.message}</div>`;
  }
}

function renderNarrative(d) {
  const content = document.getElementById('narrativeContent');
  const genAt   = document.getElementById('narrativeGeneratedAt');
  if (!content || !d.narrative) return;

  const n = d.narrative;
  const sentColors = { positive: '#10b981', neutral: '#f59e0b', negative: '#ef4444' };
  const sentLabels = { positive: '📈 良好トレンド', neutral: '➡️ 横ばい', negative: '📉 要改善' };
  const sentColor  = sentColors[n.sentiment] || '#c8a97a';

  const scqaItems = [
    { key: 'situation',   icon: '📋', label: 'Situation（現状）', color: '#3b82f6', bg: 'rgba(59,130,246,0.08)' },
    { key: 'complication',icon: '⚡', label: 'Complication（課題）', color: '#f59e0b', bg: 'rgba(245,158,11,0.08)' },
    { key: 'question',    icon: '❓', label: 'Question（問い）', color: '#8b5cf6', bg: 'rgba(139,92,246,0.08)' },
    { key: 'answer',      icon: '✅', label: 'Answer（解決策）', color: '#10b981', bg: 'rgba(16,185,129,0.08)' },
  ];

  content.innerHTML = `
    <!-- エグゼクティブサマリー -->
    <div style="padding:14px 16px;background:linear-gradient(135deg,rgba(99,102,241,0.15),rgba(30,41,59,0.5));border:1px solid rgba(99,102,241,0.25);border-radius:10px;margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
        <span style="font-size:12px;font-weight:700;color:${sentColor}">${sentLabels[n.sentiment] || ''}</span>
        <span style="font-size:10px;color:var(--text-3)">${d.clinic_name} / 直近${d.period_days}日間</span>
      </div>
      <div style="font-size:13px;color:var(--text-1);line-height:1.7">${n.executive_summary || ''}</div>
    </div>
    <!-- SCQA フレーム -->
    ${scqaItems.map(item => n[item.key] ? `
      <div class="scqa-block" style="background:${item.bg};border-color:${item.color}40">
        <div style="font-size:10px;font-weight:700;color:${item.color};letter-spacing:1px;margin-bottom:4px">${item.icon} ${item.label}</div>
        <div style="color:var(--text-1)">${Array.isArray(n[item.key]) ? n[item.key].map((s,i) => `${i+1}. ${s}`).join('<br>') : n[item.key]}</div>
      </div>
    ` : '').join('')}
    <!-- 来週のフォーカス -->
    ${n.next_week_focus ? `
    <div style="margin-top:14px;padding:12px 16px;background:rgba(200,169,122,0.1);border:1px solid rgba(200,169,122,0.3);border-radius:8px">
      <div style="font-size:10px;font-weight:700;color:#c8a97a;letter-spacing:1px;margin-bottom:4px">🎯 来週の最重要フォーカス</div>
      <div style="font-size:13px;font-weight:600;color:var(--text-1)">${n.next_week_focus}</div>
    </div>` : ''}
  `;
  if (genAt) genAt.textContent = `AI生成: ${d.generated_at}`;
}


// ============================================================
// ★ 広告文生成後に自動で心理スコアを計算（既存フローに統合）
// ============================================================
const _origGenerateAdCopy = window.generateAdCopy;
// ダッシュボードロード時にAIブリーフとナラティブを自動取得
const _origLoadDashboard = window.loadDashboard;
if (typeof loadDashboard === 'function') {
  const _origLoad = loadDashboard;
  window.loadDashboard = async function(...args) {
    await _origLoad.apply(this, args);
    // 非同期で並列取得（ダッシュボード表示を妨げない）
    setTimeout(() => {
      loadDailyBrief();
      loadNarrativeReport();
    }, 800);
  };
}

// LTVシミュレーターページ初期表示
document.addEventListener('DOMContentLoaded', () => {
  updateSimPreview();
});

// ナビゲーション切り替え時にシミュレーターを初期化
document.addEventListener('click', (e) => {
  const navItem = e.target.closest('[data-page]');
  if (navItem?.dataset.page === 'simulator') {
    setTimeout(updateSimPreview, 100);
  }
  if (navItem?.dataset.page === 'dashboard') {
    // ダッシュボードに戻った時にAIブリーフ表示
    setTimeout(() => {
      if (!_briefCache)    loadDailyBrief();
      if (!_narrativeCache) loadNarrativeReport();
    }, 300);
  }
});



// ============================================================
// ★ INDUSTRY #1 FEATURE ①: 季節性キャンペーンプランナー
// ============================================================
async function loadSeasonalCalendar() {
  const currentCards = document.getElementById('calCurrentMonthCards');
  const monthGrid    = document.getElementById('calMonthGrid');
  if (!currentCards) return;
  currentCards.innerHTML = '<div style="color:var(--text-3);font-size:12px">読み込み中...</div>';

  try {
    const d = await api(`/seasonal-calendar?clinic_id=${currentClinicId}&generate_copy=true`);
    if (!d.success) throw new Error(d.error);

    const cal = d.calendar;
    const cur = d.current;
    const nxt = d.next;

    // 今月・来月ハイライト
    currentCards.innerHTML = [
      { data: cur, label: '今月', border: '#c8a97a', bg: 'rgba(200,169,122,0.08)' },
      { data: nxt, label: '来月準備', border: '#6366f1', bg: 'rgba(99,102,241,0.06)' },
    ].map(({data, label, border, bg}) => `
      <div class="card" style="border:1px solid ${border}40;background:${bg}">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
          <span style="font-size:24px">${data.emoji}</span>
          <div>
            <span style="font-size:10px;color:var(--text-3);letter-spacing:1px">${label}</span>
            <div style="font-weight:800;font-size:15px">${data.month} — ${data.theme}</div>
          </div>
        </div>
        <div style="margin-bottom:10px">
          <div style="font-size:10px;color:var(--text-3);margin-bottom:4px">📌 主要痛み・季節症状</div>
          ${(data.seasonal_pain||[]).map(p => `<span style="font-size:11px;background:rgba(239,68,68,0.1);color:#fca5a5;padding:2px 8px;border-radius:99px;margin:2px;display:inline-block">${p}</span>`).join('')}
        </div>
        <div style="margin-bottom:10px">
          <div style="font-size:10px;color:var(--text-3);margin-bottom:4px">🎯 推奨KW</div>
          ${(data.keywords||[]).slice(0,4).map(k => `<span style="font-size:11px;background:rgba(99,102,241,0.1);color:#818cf8;padding:2px 8px;border-radius:99px;margin:2px;display:inline-block">${k}</span>`).join('')}
        </div>
        <div>
          <div style="font-size:10px;color:var(--text-3);margin-bottom:4px">⚡ 入札推奨</div>
          ${(data.bid_boost||[]).map(b => `<div style="font-size:11px;color:#10b981">▲ ${b}</div>`).join('')}
        </div>
      </div>`).join('');

    // 12ヶ月グリッド
    monthGrid.innerHTML = Object.entries(cal).map(([m, data]) => `
      <div class="card" style="cursor:pointer;transition:border-color 0.2s;border:1px solid rgba(255,255,255,0.06)" onmouseover="this.style.borderColor='rgba(200,169,122,0.4)'" onmouseout="this.style.borderColor='rgba(255,255,255,0.06)'">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
          <span style="font-size:20px">${data.emoji}</span>
          <div>
            <div style="font-weight:700;font-size:13px">${data.month}</div>
            <div style="font-size:10px;color:var(--text-3)">${data.theme}</div>
          </div>
        </div>
        <div style="font-size:11px;color:#c8a97a;background:rgba(200,169,122,0.1);padding:4px 8px;border-radius:6px;margin-bottom:6px">
          💡 ${data.copy_angle}
        </div>
        <div style="font-size:10px;color:var(--text-3)">${(data.seasonal_pain||[]).slice(0,2).join(' / ')}</div>
      </div>`).join('');

    // AI生成コピー表示
    if (d.ai_copy) {
      const aiCard = document.getElementById('calAiCopyCard');
      const aiContent = document.getElementById('calAiCopyContent');
      if (aiCard && aiContent) {
        aiCard.style.display = 'block';
        const hd = d.ai_copy.headlines || [];
        const ds = d.ai_copy.descriptions || [];
        aiContent.innerHTML = `
          <div style="font-size:12px;color:var(--text-3);margin-bottom:8px">🗓 ${cur.month}（${cur.theme}）向けAI生成</div>
          <div style="background:#0f172a;border-radius:10px;padding:14px;border:1px solid rgba(255,255,255,0.06);margin-bottom:12px">
            <div style="font-size:11px;color:var(--text-3);margin-bottom:6px">見出し</div>
            ${hd.map((h,i) => `<div style="font-size:13px;color:#818cf8;margin-bottom:4px">${i+1}. ${h}</div>`).join('')}
            <div style="font-size:11px;color:var(--text-3);margin:10px 0 6px">説明文</div>
            ${ds.map((t,i) => `<div style="font-size:12px;color:var(--text-2);margin-bottom:4px">${i+1}. ${t}</div>`).join('')}
          </div>
          <button class="btn btn-primary" onclick="applySeasonalCopy(${JSON.stringify(hd)}, ${JSON.stringify(ds)})">
            この広告文を「AI広告文」ページで使う
          </button>`;
      }
    }
  } catch(e) {
    currentCards.innerHTML = `<div style="color:var(--danger)">${e.message}</div>`;
  }
}

function applySeasonalCopy(headlines, descriptions) {
  // 広告文ページに移動してデータを渡す
  window._seasonalCopyPreload = { headlines, descriptions };
  navigateTo('ad-copy');
  toast('季節広告文を広告文ページにセットしました ✅', 'success', 3000);
}


// ============================================================
// ★ INDUSTRY #1 FEATURE ②: 時間帯×曜日ヒートマップ
// ============================================================
async function loadHeatmap() {
  const grid    = document.getElementById('heatmapGrid');
  const btn     = document.getElementById('heatmapLoadBtn');
  const peakSum = document.getElementById('heatmapPeakSummary');
  if (!grid) return;

  btn.disabled = true;
  btn.textContent = '⏳ 生成中...';
  grid.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text-3)"><div class="spinner" style="width:20px;height:20px;margin:0 auto 8px"></div>24h×7日のデータを解析中...</div>';

  try {
    const d = await api(`/performance-heatmap?clinic_id=${currentClinicId}`);
    if (!d.success) throw new Error(d.error);

    const dows   = d.dow_names;
    const maxCtr = d.max_ctr;
    const grid_data = d.grid;

    // ヒートマップ描画
    let html = `<div style="display:grid;grid-template-columns:40px repeat(7,1fr);gap:2px;font-size:10px">`;
    // ヘッダ
    html += `<div></div>`;
    dows.forEach(d => { html += `<div style="text-align:center;color:var(--text-3);padding:4px 0;font-weight:700">${d}</div>`; });

    // 各時間帯
    for (let h = 0; h < 24; h++) {
      html += `<div style="color:var(--text-3);padding:2px 4px;display:flex;align-items:center;justify-content:flex-end;font-size:10px">${String(h).padStart(2,'0')}</div>`;
      for (let dow = 0; dow < 7; dow++) {
        const cell = grid_data[dow] && grid_data[dow][h] ? grid_data[dow][h] : {ctr:0,cvr:0};
        const intensity = cell.ctr / maxCtr;
        const alpha = 0.15 + intensity * 0.75;
        const r = Math.round(200 * intensity + 30 * (1-intensity));
        const g = Math.round(50  * (1-intensity));
        const b = Math.round(50  * (1-intensity));
        const textColor = intensity > 0.6 ? '#fff' : 'rgba(255,255,255,0.5)';
        html += `<div title="${dows[dow]}曜${h}時 CTR:${cell.ctr}% CVR:${cell.cvr}%" style="background:rgba(${r},${g},${b},${alpha.toFixed(2)});border-radius:3px;padding:3px 0;text-align:center;color:${textColor};cursor:default">${cell.ctr > 0 ? cell.ctr.toFixed(1) : '-'}</div>`;
      }
    }
    html += `</div>`;
    // 凡例
    html += `<div style="display:flex;align-items:center;gap:8px;margin-top:12px;font-size:11px;color:var(--text-3)">
      <span>低</span>
      <div style="width:120px;height:8px;border-radius:99px;background:linear-gradient(to right,rgba(30,50,50,0.5),rgba(200,50,50,0.9))"></div>
      <span>高 CTR</span>
      <span style="margin-left:12px;color:var(--text-3)">数値=CTR(%)</span>
    </div>`;
    grid.innerHTML = html;

    peakSum.textContent = d.bid_schedule?.peak_time_summary || '';

    // 入札推奨カード
    const bidCards = document.getElementById('heatmapBidCards');
    if (bidCards && d.bid_schedule) {
      const bs = d.bid_schedule;
      bidCards.innerHTML = `
        <div class="card" style="border:1px solid rgba(16,185,129,0.3)">
          <h3 class="card-title" style="color:#10b981">⬆ 入札引き上げ推奨時間帯</h3>
          <div style="display:flex;flex-direction:column;gap:6px">
            ${(bs.high_bid_slots||[]).slice(0,6).map(s => `
              <div style="display:flex;justify-content:space-between;align-items:center;font-size:12px;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.04)">
                <span style="color:var(--text-2)">${s.dow}曜 ${s.hour}</span>
                <span style="color:#fca5a5;font-size:10px">CTR ${s.ctr}%</span>
                <span style="background:rgba(16,185,129,0.2);color:#10b981;padding:2px 8px;border-radius:99px;font-weight:700;font-size:11px">${s.recommendation}</span>
              </div>`).join('')}
          </div>
        </div>
        <div class="card" style="border:1px solid rgba(239,68,68,0.3)">
          <h3 class="card-title" style="color:#ef4444">⬇ 入札削減推奨時間帯</h3>
          <div style="display:flex;flex-direction:column;gap:6px">
            ${(bs.reduce_bid_slots||[]).map(s => `
              <div style="display:flex;justify-content:space-between;align-items:center;font-size:12px;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.04)">
                <span style="color:var(--text-2)">${s.dow}曜 ${s.hour}</span>
                <span style="color:var(--text-3);font-size:10px">CTR ${s.ctr}%</span>
                <span style="background:rgba(239,68,68,0.2);color:#ef4444;padding:2px 8px;border-radius:99px;font-weight:700;font-size:11px">${s.recommendation}</span>
              </div>`).join('')}
          </div>
        </div>`;
    }

    // AIインサイト
    const insightCard = document.getElementById('heatmapInsightCard');
    const insightText = document.getElementById('heatmapInsightText');
    if (insightCard && insightText && d.ai_insight) {
      insightCard.style.display = 'block';
      insightText.textContent = d.ai_insight;
    }

    toast('ヒートマップ生成完了 ✅', 'success', 2000);
  } catch(e) {
    grid.innerHTML = `<div style="color:var(--danger);padding:20px">${e.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '🔥 再生成';
  }
}


// ============================================================
// ★ INDUSTRY #1 FEATURE ③: 除外KW AIスキャナー
// ============================================================
async function runNegativeKwScan() {
  const results  = document.getElementById('kwScanResults');
  const summary  = document.getElementById('kwScanSummary');
  const btn      = document.getElementById('kwScanBtn');
  if (!results) return;

  btn.disabled = true;
  btn.textContent = '⏳ 業界パターンと照合中...';
  results.innerHTML = '<div style="display:flex;align-items:center;gap:10px;padding:30px;color:var(--text-3);justify-content:center"><div class="spinner" style="width:16px;height:16px"></div><span>500+パターンと照合中...</span></div>';

  try {
    const d = await api(`/negative-kw/ai-scan?clinic_id=${currentClinicId}`, { method: 'POST' });
    if (!d.success) throw new Error(d.error);

    summary.innerHTML = `<span style="background:rgba(239,68,68,0.1);color:#fca5a5;padding:3px 10px;border-radius:99px;font-weight:700">⚠️ 未設定リスクKW: ${d.total_risk_keywords}件</span> <span style="color:var(--text-3)">（ライブラリ合計: ${d.library_total}件中）</span>`;

    results.innerHTML = d.scan_results.map(cat => `
      <div class="card" style="margin-bottom:12px;border:1px solid ${cat.color}20">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px">
          <div style="display:flex;align-items:center;gap:8px">
            <span style="font-weight:700;font-size:13px">${cat.label}</span>
            <span style="font-size:10px;padding:2px 8px;border-radius:99px;font-weight:700;background:${cat.color}20;color:${cat.color}">リスク: ${cat.risk_level}</span>
          </div>
          <div style="display:flex;gap:8px;align-items:center">
            <span style="font-size:11px;color:var(--text-3)">未設定 ${cat.missing_count}件</span>
            <button onclick="bulkAddNegativeKws(${JSON.stringify(cat.missing_keywords)}, '${cat.category_key}')" style="font-size:11px;font-weight:700;padding:5px 14px;background:${cat.color}20;color:${cat.color};border:1px solid ${cat.color}40;border-radius:99px;cursor:pointer">
              ✚ 全件追加
            </button>
          </div>
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:6px">
          ${cat.missing_keywords.map(kw => `
            <span style="font-size:11px;background:rgba(255,255,255,0.04);color:var(--text-2);padding:3px 10px;border-radius:99px;border:1px solid rgba(255,255,255,0.08);cursor:pointer" onclick="addSingleNegativeKw('${kw.replace(/'/g,"\\'")}')">
              ${kw} <span style="color:var(--text-3)">+</span>
            </span>`).join('')}
        </div>
      </div>`).join('');

    // AI追加提案
    if (d.ai_additional && d.ai_additional.length > 0) {
      const card = document.getElementById('kwAiSuggestCard');
      const content = document.getElementById('kwAiSuggestContent');
      if (card && content) {
        card.style.display = 'block';
        content.innerHTML = `<div style="display:flex;flex-direction:column;gap:8px">
          ${d.ai_additional.map(item => `
            <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 14px;background:#0f172a;border-radius:8px;flex-wrap:wrap;gap:8px">
              <div>
                <span style="font-weight:700;font-size:12px;color:#818cf8">${item.keyword}</span>
                <span style="font-size:10px;color:var(--text-3);margin-left:8px">${item.reason}</span>
              </div>
              <div style="display:flex;gap:8px;align-items:center">
                ${item.estimated_waste ? `<span style="font-size:10px;color:#10b981;background:rgba(16,185,129,0.1);padding:2px 8px;border-radius:99px">${item.estimated_waste}</span>` : ''}
                <button onclick="addSingleNegativeKw('${(item.keyword||'').replace(/'/g,"\\'")}')\" style="font-size:11px;padding:3px 10px;background:rgba(99,102,241,0.2);color:#818cf8;border:1px solid rgba(99,102,241,0.3);border-radius:99px;cursor:pointer">追加</button>
              </div>
            </div>`).join('')}
        </div>`;
      }
    }

    toast(`スキャン完了 ✅ 未設定リスクKW: ${d.total_risk_keywords}件`, 'success', 4000);
  } catch(e) {
    results.innerHTML = `<div style="color:var(--danger);padding:20px">${e.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '🔍 再スキャン';
  }
}

async function bulkAddNegativeKws(keywords, category) {
  let added = 0;
  for (const kw of keywords) {
    try {
      await api('/negative-keywords', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clinic_id: currentClinicId, keyword: kw, match_type: 'EXACT', campaign_id: null }),
      });
      added++;
    } catch(e) {}
  }
  toast(`${added}件の除外KWを一括追加しました ✅`, 'success', 3000);
}

async function addSingleNegativeKw(keyword) {
  try {
    await api('/negative-keywords', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clinic_id: currentClinicId, keyword, match_type: 'EXACT', campaign_id: null }),
    });
    toast(`「${keyword}」を除外KWに追加 ✅`, 'success', 2000);
  } catch(e) {
    toast('追加失敗: ' + e.message, 'error');
  }
}


// ============================================================
// ★ INDUSTRY #1 FEATURE ④: 広告健全度スコアカード
// ============================================================
async function loadScorecard() {
  const summary = document.getElementById('scorecardSummary');
  const btn     = document.getElementById('scorecardBtn');
  const genAt   = document.getElementById('scorecardGeneratedAt');
  if (!summary) return;

  btn.disabled = true;
  btn.textContent = '⏳ 診断中...';

  try {
    const d = await api(`/scorecard?clinic_id=${currentClinicId}`);
    if (!d.success) throw new Error(d.error);

    const gradeColors = { S:'#f59e0b', A:'#10b981', B:'#3b82f6', C:'#8b5cf6', D:'#ef4444' };
    const gc = gradeColors[d.grade] || '#c8a97a';

    document.getElementById('scorecardGrade').textContent = d.grade;
    document.getElementById('scorecardGrade').style.color = gc;
    document.getElementById('scorecardTotal').textContent = `${d.total_score} / ${d.max_score}点`;
    document.getElementById('scorecardTotal').style.color = 'var(--text-2)';

    // 10軸バー
    const barsEl = document.getElementById('scorecardBars');
    barsEl.innerHTML = Object.entries(d.scores).map(([key, s]) => {
      const pct = Math.round(s.score / 10 * 100);
      const barColor = s.score >= 8 ? '#10b981' : s.score >= 5 ? '#f59e0b' : '#ef4444';
      return `
        <div style="display:grid;grid-template-columns:120px 1fr 36px 160px;align-items:center;gap:8px;font-size:11px">
          <span style="color:var(--text-2)">${s.icon} ${s.label}</span>
          <div style="height:6px;background:rgba(255,255,255,0.08);border-radius:99px;overflow:hidden">
            <div style="width:${pct}%;height:100%;background:${barColor};border-radius:99px;transition:width 0.7s ease"></div>
          </div>
          <span style="color:${barColor};font-weight:700">${s.score}</span>
          <span style="color:var(--text-3);font-size:10px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">${s.fix}</span>
        </div>`;
    }).join('');

    // ロードマップ
    const roadmapEl = document.getElementById('scorecardRoadmap');
    const months = [
      { key: 'month1', label: '📌 Month1 最優先', color: '#ef4444', bg: 'rgba(239,68,68,0.08)' },
      { key: 'month2', label: '📌 Month2 重要',   color: '#f59e0b', bg: 'rgba(245,158,11,0.08)' },
      { key: 'month3', label: '📌 Month3 仕上げ', color: '#10b981', bg: 'rgba(16,185,129,0.08)' },
    ];
    roadmapEl.innerHTML = months.map(m => `
      <div class="card" style="border:1px solid ${m.color}30;background:${m.bg}">
        <div style="font-size:11px;font-weight:700;color:${m.color};margin-bottom:10px;letter-spacing:0.5px">${m.label}</div>
        ${(d.roadmap[m.key]||[]).map(item => `
          <div style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05)">
            <div style="font-size:12px;font-weight:700">${item.icon} ${item.label} <span style="color:${m.color}">（${item.score}/10）</span></div>
            <div style="font-size:11px;color:var(--text-3);margin-top:2px">${item.fix}</div>
          </div>`).join('')}
      </div>`).join('');

    summary.style.display = 'block';
    if (genAt) genAt.textContent = `診断: ${d.generated_at}`;
    toast(`診断完了 Grade ${d.grade} (${d.total_score}/100点) ✅`, 'success', 4000);
  } catch(e) {
    toast('診断失敗: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '📊 再診断';
  }
}


// ページ切り替え時のトリガー追加
document.addEventListener('click', function _industryTrigger(e) {
  const nav = e.target.closest('[data-page]');
  if (!nav) return;
  const page = nav.dataset.page;
  if (page === 'calendar') setTimeout(loadSeasonalCalendar, 100);
  if (page === 'scorecard') setTimeout(() => {
    const sum = document.getElementById('scorecardSummary');
    if (sum && sum.style.display === 'none' && document.getElementById('scorecardGrade').textContent === '') {
      loadScorecard();
    }
  }, 100);
});



// ============================================================
// ★ AI予算自動配分ページ
// ============================================================

// 予算ページを開いた時、保存済み月間予算を復元して表示
async function loadBudgetPage() {
  try {
    const acc = await api(`/ads-account?clinic_id=${currentClinicId}`);
    const saved = acc?.settings?.monthly_budget_yen;
    if (saved) {
      const inp = document.getElementById('monthlyBudgetInput');
      if (inp) inp.value = saved;
      // 最終配分日を表示
      const lastEl = document.getElementById('lastAllocatedAt');
      if (lastEl && acc?.settings?.last_allocated_at) {
        lastEl.textContent = `最終AI配分: ${acc.settings.last_allocated_at}`;
      }
    }
  } catch(e) {}
}

// 月間予算設定 + AI配分実行
window.setAndAllocateBudget = async function setAndAllocateBudget() {
  const inp = document.getElementById('monthlyBudgetInput');
  const btn = document.getElementById('budgetAllocBtn');
  const loading = document.getElementById('budgetAllocLoading');
  const result  = document.getElementById('budgetAllocResult');
  const autoToggle = document.getElementById('autoAllocateToggle');

  const monthly = parseInt(inp?.value || '0', 10);
  if (!monthly || monthly < 10000) {
    toast('月間予算は10,000円以上で入力してください', 'error'); return;
  }

  btn.disabled = true;
  btn.textContent = '⏳ AIが計算中...';
  if (loading) loading.style.display = 'block';
  if (result)  result.style.display  = 'none';

  try {
    const d = await api('/budget/monthly-target', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        clinic_id: currentClinicId,
        monthly_budget_yen: monthly,
        ai_auto_allocate: autoToggle?.checked !== false,
      }),
    });

    if (!d.success) throw new Error(d.error || '配分失敗');
    renderBudgetAllocation(d.allocation, monthly);
    toast(`✅ 月間予算¥${monthly.toLocaleString()}を設定。AIが配分しました`, 'success', 4000);
  } catch(e) {
    toast('配分エラー: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '🤖 AIで最適配分する';
    if (loading) loading.style.display = 'none';
  }
};

function renderBudgetAllocation(alloc, monthlyBudget) {
  if (!alloc || !alloc.allocations) return;
  const resultEl = document.getElementById('budgetAllocResult');
  if (resultEl) resultEl.style.display = 'block';

  // AIコメント
  const commentEl = document.getElementById('aiAllocComment');
  if (commentEl) commentEl.textContent = alloc.ai_comment || '';

  // メタ情報
  const metaEl = document.getElementById('budgetSummaryMeta');
  if (metaEl) {
    metaEl.textContent = `月間¥${(monthlyBudget||0).toLocaleString()} | 残り${alloc.remaining_days}日 | ${alloc.total_campaigns}キャンペーン | ${alloc.allocated_at}`;
  }

  // 最終配分日を更新
  const lastEl = document.getElementById('lastAllocatedAt');
  if (lastEl) lastEl.textContent = `最終AI配分: ${alloc.allocated_at}`;

  // グレードカラー
  const gradeColor = { S:'#f59e0b', A:'#10b981', B:'#3b82f6', C:'#f97316' };
  const barColors  = ['#c8a97a','#818cf8','#34d399','#f472b6','#60a5fa','#fb923c'];

  // 配分バー
  const barsEl = document.getElementById('budgetAllocBars');
  if (barsEl) {
    barsEl.innerHTML = alloc.allocations.map((a, i) => {
      const c = barColors[i % barColors.length];
      const gc = gradeColor[a.roi_grade] || '#888';
      return `
        <div>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;flex-wrap:wrap;gap:6px">
            <div style="display:flex;align-items:center;gap:8px">
              <span style="font-size:11px;font-weight:800;padding:2px 8px;border-radius:99px;background:${gc}20;color:${gc}">${a.roi_grade}</span>
              <span style="font-size:13px;font-weight:700">${a.campaign_name}</span>
            </div>
            <div style="display:flex;align-items:center;gap:10px;font-size:12px">
              <span style="color:${c};font-weight:700">¥${(a.monthly_alloc_yen||0).toLocaleString()}/月</span>
              <span style="color:var(--text-3)">日¥${(a.daily_budget_yen||0).toLocaleString()}</span>
              <span style="color:var(--text-3)">${a.share_pct}%</span>
            </div>
          </div>
          <div style="height:8px;background:rgba(255,255,255,0.07);border-radius:99px;overflow:hidden">
            <div style="width:${a.share_pct}%;height:100%;background:${c};border-radius:99px;transition:width 0.8s ease"></div>
          </div>
          <div style="font-size:11px;color:var(--text-3);margin-top:3px">${a.reason}</div>
        </div>`;
    }).join('');
  }

  // 詳細テーブル
  const tblEl = document.getElementById('budgetAllocTable');
  if (tblEl) {
    tblEl.innerHTML = alloc.allocations.map((a, i) => {
      const gc = gradeColor[a.roi_grade] || '#888';
      return `
        <tr style="border-bottom:1px solid rgba(255,255,255,0.05)">
          <td style="padding:10px 12px;font-weight:600">${a.campaign_name}</td>
          <td style="padding:10px 12px;text-align:right;color:#c8a97a;font-weight:700">¥${(a.monthly_alloc_yen||0).toLocaleString()}</td>
          <td style="padding:10px 12px;text-align:right">¥${(a.daily_budget_yen||0).toLocaleString()}</td>
          <td style="padding:10px 12px;text-align:right">${a.share_pct}%</td>
          <td style="padding:10px 12px;text-align:center">
            <span style="font-size:11px;font-weight:800;padding:2px 10px;border-radius:99px;background:${gc}20;color:${gc}">${a.roi_grade}</span>
          </td>
          <td style="padding:10px 12px;color:var(--text-2)">¥${(a.est_cpa||0).toLocaleString()}</td>
          <td style="padding:10px 12px;color:var(--text-3);font-size:11px">${a.reason}</td>
        </tr>`;
    }).join('');
  }
}

// ページ切り替えイベントに予算ページを追加
document.addEventListener('click', function _budgetPageTrigger(e) {
  const nav = e.target.closest('[data-page]');
  if (nav && nav.dataset.page === 'budget') {
    setTimeout(loadBudgetPage, 100);
    document.removeEventListener('click', _budgetPageTrigger);
    // 再登録（次回アクセス用）
    setTimeout(() => {
      document.addEventListener('click', function _budgetPageTrigger2(e2) {
        const nav2 = e2.target.closest('[data-page]');
        if (nav2 && nav2.dataset.page === 'budget') setTimeout(loadBudgetPage, 100);
      });
    }, 200);
  }
});



// ============================================================
// ★ プラン制限制御
// ============================================================

/**
 * ログイン後にプラン情報を適用する。
 * - STARTERプラン → Yahooボタンにロックアイコンを表示・グレーアウト
 * - STANDARDプラン → 通常表示
 */
function applyPlanRestrictions(data) {
  const yahooEnabled = data.yahoo_enabled !== false;
  const planType     = data.plan_type || 'standard';
  const planName     = data.plan_name || 'スタンダード';

  const btnYahoo = document.getElementById('btnYahoo');
  if (!btnYahoo) return;

  if (!yahooEnabled || planType === 'starter') {
    // ロック表示
    btnYahoo.style.opacity     = '0.4';
    btnYahoo.style.cursor      = 'not-allowed';
    btnYahoo.style.filter      = 'grayscale(60%)';
    btnYahoo.title             = `Yahoo!広告はSTANDARDプラン以上で利用できます（現在: ${planName}）`;

    // ボタンラベルにカギアイコン追加（二重追加防止）
    if (!btnYahoo.querySelector('.lock-icon')) {
      const lock = document.createElement('span');
      lock.className   = 'lock-icon';
      lock.textContent = ' 🔒';
      lock.style.fontSize = '10px';
      btnYahoo.appendChild(lock);
    }
  } else {
    // ロック解除
    btnYahoo.style.opacity = '';
    btnYahoo.style.cursor  = '';
    btnYahoo.style.filter  = '';
    btnYahoo.title         = '';
    const lock = btnYahoo.querySelector('.lock-icon');
    if (lock) lock.remove();
  }

  // サイドバー下部にプランバッジを表示
  _renderPlanBadge(planName, planType, yahooEnabled);
}

function _renderPlanBadge(planName, planType, yahooEnabled) {
  // 既存バッジの削除
  document.getElementById('sidebarPlanBadge')?.remove();

  const sidebar = document.querySelector('.sidebar') || document.querySelector('nav.sidebar') || document.getElementById('sidebar');
  if (!sidebar) return;

  const badge = document.createElement('div');
  badge.id = 'sidebarPlanBadge';
  badge.style.cssText = `
    margin: 8px 12px 4px;
    padding: 6px 10px;
    border-radius: 8px;
    font-size: 11px;
    font-weight: 700;
    background: ${planType === 'starter' ? 'rgba(251,191,36,0.12)' : 'rgba(99,102,241,0.12)'};
    color: ${planType === 'starter' ? '#fbbf24' : '#818cf8'};
    border: 1px solid ${planType === 'starter' ? 'rgba(251,191,36,0.3)' : 'rgba(99,102,241,0.3)'};
    text-align: center;
    letter-spacing: 0.5px;
  `;
  badge.textContent = planType === 'starter'
    ? `📋 ${planName} (Google専用)`
    : `⭐ ${planName} (Google+Yahoo)`;

  // ログアウトボタンの直前に挿入
  const logoutBtn = document.getElementById('logoutBtn');
  if (logoutBtn) {
    sidebar.insertBefore(badge, logoutBtn);
  } else {
    sidebar.appendChild(badge);
  }
}

// 既存トークンでの自動復元時にも適用（ページリロード時）
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => {
    const user = getUser();
    if (user?.email && user?.plan_type) {
      applyPlanRestrictions(user);
    }
  }, 300);
});



// ============================================================
// ★ 管理者: LP問い合わせ一覧管理
// ============================================================

async function loadInquiries() {
  const tbody = document.getElementById('inquiryTableBody');
  const empty = document.getElementById('inquiryEmpty');
  const count = document.getElementById('inquiryCount');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--text-3)"><div class="spinner" style="width:18px;height:18px;margin:0 auto"></div></td></tr>';

  try {
    const d = await api('/admin/inquiries');
    const list = d.inquiries || [];
    if (count) count.textContent = `${list.length}件`;

    if (list.length === 0) {
      tbody.innerHTML = '';
      if (empty) empty.style.display = 'block';
      return;
    }
    if (empty) empty.style.display = 'none';

    const statusColors = { new: '#fbbf24', done: '#10b981', contacted: '#3b82f6' };
    const statusLabels = { new: '新規', done: '対応済', contacted: '連絡済' };

    tbody.innerHTML = list.map(r => {
      const sc = statusColors[r.status] || '#888';
      const sl = statusLabels[r.status] || r.status;
      return `
        <tr style="border-bottom:1px solid rgba(255,255,255,0.05)">
          <td style="padding:8px 10px;color:var(--text-3);white-space:nowrap">${(r.created_at || '').replace('T',' ').slice(0,16)}</td>
          <td style="padding:8px 10px;font-weight:600">${r.name}</td>
          <td style="padding:8px 10px">${r.clinic}</td>
          <td style="padding:8px 10px">${r.area}</td>
          <td style="padding:8px 10px"><a href="mailto:${r.email}" style="color:var(--accent);text-decoration:none">${r.email}</a></td>
          <td style="padding:8px 10px;color:var(--text-3)">${r.ads_status || '—'}</td>
          <td style="padding:8px 10px;text-align:center">
            <select onchange="updateInquiryStatus(${r.id}, this.value)"
              style="background:#1a1a2e;border:1px solid rgba(255,255,255,0.1);color:${sc};padding:4px 8px;border-radius:6px;font-size:11px;font-weight:700;cursor:pointer">
              <option value="new"       ${r.status === 'new'       ? 'selected' : ''} style="color:#fbbf24">新規</option>
              <option value="contacted" ${r.status === 'contacted' ? 'selected' : ''} style="color:#3b82f6">連絡済</option>
              <option value="done"      ${r.status === 'done'      ? 'selected' : ''} style="color:#10b981">対応済</option>
            </select>
          </td>
        </tr>`;
    }).join('');
  } catch(e) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--text-3)">読み込みエラー</td></tr>`;
  }
}
window.loadInquiries = loadInquiries;

window.updateInquiryStatus = async function(id, status) {
  try {
    await api(`/admin/inquiries/${id}?status=${status}`, { method: 'PATCH' });
    toast(`ステータスを「${status === 'new' ? '新規' : status === 'contacted' ? '連絡済' : '対応済'}」に変更しました`, 'success', 2000);
  } catch(e) {
    toast('更新失敗', 'error');
  }
};

