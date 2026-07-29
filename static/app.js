/* app.js - X Automation Bot Dashboard Client Logic */

document.addEventListener('DOMContentLoaded', () => {
  // ── Authentication & Fetch Wrapper ─────────────────────────────────
  const authModal = document.getElementById('auth-modal');
  const formAuth = document.getElementById('form-auth');
  const authTokenInput = document.getElementById('auth-token-input');
  const btnLogout = document.getElementById('btn-logout');

  function getAuthToken() {
    return localStorage.getItem('auth_token') || '';
  }

  function showAuthModal() {
    if (authModal) authModal.style.display = 'flex';
    if (btnLogout) btnLogout.style.display = 'none';
  }

  function hideAuthModal() {
    if (authModal) authModal.style.display = 'none';
    if (btnLogout) btnLogout.style.display = 'inline-flex';
  }

  const originalFetch = window.fetch;
  window.fetch = async function (url, options = {}) {
    options.headers = options.headers || {};
    const token = getAuthToken();

    if (typeof url === 'string' && url.startsWith('/api/')) {
      if (token) {
        if (options.headers instanceof Headers) {
          options.headers.set('Authorization', `Bearer ${token}`);
        } else {
          options.headers['Authorization'] = `Bearer ${token}`;
        }
      }
    }

    const response = await originalFetch(url, options);
    if (response.status === 401 && typeof url === 'string' && url.startsWith('/api/')) {
      localStorage.removeItem('auth_token');
      showAuthModal();
    }
    return response;
  };

  if (formAuth) {
    formAuth.addEventListener('submit', (e) => {
      e.preventDefault();
      const token = authTokenInput ? authTokenInput.value.trim() : '';
      if (token) {
        localStorage.setItem('auth_token', token);
        hideAuthModal();
        fetchStatus();
        fetchHistory();
      }
    });
  }

  if (btnLogout) {
    btnLogout.addEventListener('click', () => {
      localStorage.removeItem('auth_token');
      showAuthModal();
      showToast('Dashboard locked', 'info');
    });
  }

  if (!getAuthToken()) {
    showAuthModal();
  } else {
    hideAuthModal();
  }

  // ── DOM References ──────────────────────────────────────────────────
  const tabButtons = document.querySelectorAll('.tab-btn');
  const tabPanels = document.querySelectorAll('.tab-panel');
  
  // Status Header Badges
  const btnPause = document.getElementById('btn-pause');
  const statusDot = document.getElementById('status-dot');
  const statusLabel = document.getElementById('status-label');
  const queueDepth = document.getElementById('queue-depth');
  const lastRun = document.getElementById('last-run');
  const btnRunNow = document.getElementById('btn-run-now');
  
  // Dashboard Tab
  const btnRefreshHistory = document.getElementById('btn-refresh-history');
  const historyGrid = document.getElementById('history-grid');
  
  // Quick Post Widget (Task 3.3)
  const formQuickPost = document.getElementById('form-quick-post');
  const qpUrl = document.getElementById('qp-url');
  const qpAccount = document.getElementById('qp-account');
  const qpCaption = document.getElementById('qp-caption');
  const qpSubmitBtn = document.getElementById('qp-submit-btn');
  
  // Sources Tab
  const formAddSource = document.getElementById('form-add-source');
  const sourceUrl = document.getElementById('source-url');
  const sourcePlatform = document.getElementById('source-platform');
  const sourceName = document.getElementById('source-name');
  const sourcesList = document.getElementById('sources-list');
  
  // Accounts Tab
  const formAddAccount = document.getElementById('form-add-account');
  const acctPlatform = document.getElementById('acct-platform');
  const acctLabel = document.getElementById('acct-label');
  const acctAuthMode = document.getElementById('acct-auth-mode');
  
  // X fields
  const acctApiFields = document.getElementById('acct-api-fields');
  const acctCookieFields = document.getElementById('acct-cookie-fields');
  const acctApiKey = document.getElementById('acct-api-key');
  const acctApiSecret = document.getElementById('acct-api-secret');
  const acctAccessToken = document.getElementById('acct-access-token');
  const acctAccessSecret = document.getElementById('acct-access-secret');
  const acctCookieAuthToken = document.getElementById('acct-cookie-auth-token');
  const acctCookieCt0 = document.getElementById('acct-cookie-ct0');
  
  // Instagram fields
  const acctInstagramApiFields = document.getElementById('acct-instagram-api-fields');
  const acctInstagramCookieFields = document.getElementById('acct-instagram-cookie-fields');
  const acctIgAccessToken = document.getElementById('acct-ig-access-token');
  const acctIgAccountId = document.getElementById('acct-ig-account-id');
  const acctIgUsername = document.getElementById('acct-ig-username');
  const acctIgPassword = document.getElementById('acct-ig-password');
  
  // TikTok fields
  const acctTikTokApiFields = document.getElementById('acct-tiktok-api-fields');
  const acctTikTokCookieFields = document.getElementById('acct-tiktok-cookie-fields');
  const acctTtAccessToken = document.getElementById('acct-tt-access-token');
  const acctTtOpenId = document.getElementById('acct-tt-open-id');
  const acctTtSessionId = document.getElementById('acct-tt-session-id');
  const acctTtUserAgent = document.getElementById('acct-tt-user-agent');

  const acctYoutubeOauthFields = document.getElementById('acct-youtube-oauth-fields');
  const acctYtClientId = document.getElementById('acct-yt-client-id');
  const acctYtClientSecret = document.getElementById('acct-yt-client-secret');
  const acctYtRefreshToken = document.getElementById('acct-yt-refresh-token');

  const accountsList = document.getElementById('accounts-list');
  
  // Settings Tab
  const formSettings = document.getElementById('form-settings');
  const settingInterval = document.getElementById('setting-interval');
  const intervalValue = document.getElementById('interval-value');
  const settingCaptionAi = document.getElementById('setting-caption-ai');
  const settingGeminiKey = document.getElementById('setting-gemini-key');
  const settingTemplate = document.getElementById('setting-template');
  
  // Logs Tab
  const btnClearLogs = document.getElementById('btn-clear-logs');
  const logAutoscroll = document.getElementById('log-autoscroll');
  const logTerminal = document.getElementById('log-terminal');
  const logContent = document.getElementById('log-content');
  const logFilterScheduler = document.getElementById('log-filter-scheduler');
  const logFilterDownloader = document.getElementById('log-filter-downloader');
  const logFilterPublisher = document.getElementById('log-filter-publisher');
  const logFilterError = document.getElementById('log-filter-error');
  
  // Filters and Quick Post Platforms
  const qpPlatform = document.getElementById('qp-platform');
  const historyPlatformFilter = document.getElementById('history-platform-filter');

  // State
  let activeTab = 'dashboard';
  let pollIntervalId = null;
  let logIntervalId = null;
  
  // ── Toast Notifications ──────────────────────────────────────────────
  function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerText = message;
    
    container.appendChild(toast);
    
    // Auto-remove toast
    setTimeout(() => {
      toast.style.animation = 'toastIn 0.3s cubic-bezier(0.16, 1, 0.3, 1) reverse forwards';
      toast.addEventListener('animationend', () => {
        toast.remove();
      });
    }, 4000);
  }

  // Helper: Format relative or absolute timestamp
  function formatTimestamp(isoStr) {
    if (!isoStr) return 'Never';
    try {
      let parsedStr = isoStr;
      if (parsedStr && !parsedStr.includes('Z') && !parsedStr.includes('+')) {
        parsedStr = parsedStr.replace(' ', 'T') + 'Z';
      }
      const date = new Date(parsedStr);
      if (isNaN(date.getTime())) return isoStr;
      
      const now = new Date();
      const diffMs = now - date;
      const diffMin = Math.round(diffMs / 60000);
      
      if (diffMin < 1) return 'Just now';
      if (diffMin < 60) return `${diffMin}m ago`;
      
      const diffHr = Math.round(diffMin / 60);
      if (diffHr < 24) return `${diffHr}h ago`;
      
      return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch (e) {
      return isoStr;
    }
  }

  // ── Tab Navigation Logic ─────────────────────────────────────────────
  function switchTab(tabId) {
    activeTab = tabId;
    
    // Update active tab buttons
    tabButtons.forEach(btn => {
      if (btn.getAttribute('data-tab') === tabId) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });
    
    // Show corresponding panel
    tabPanels.forEach(panel => {
      if (panel.id === `panel-${tabId}`) {
        panel.classList.add('active');
      } else {
        panel.classList.remove('active');
      }
    });
    
    // Action when loading a specific tab
    if (tabId === 'dashboard') {
      fetchHistory();
    } else if (tabId === 'sources') {
      fetchSources();
    } else if (tabId === 'accounts') {
      fetchAccounts();
    } else if (tabId === 'settings') {
      fetchSettings();
    } else if (tabId === 'logs') {
      fetchLogs();
      startLogPolling();
    }
    
    // Stop log polling if we navigate away from logs tab
    if (tabId !== 'logs') {
      stopLogPolling();
    }
  }

  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      switchTab(btn.getAttribute('data-tab'));
    });
  });

  // ── Status Header Badges Polling ─────────────────────────────────────
  async function fetchStatus() {
    try {
      const res = await fetch('/api/status');
      if (!res.ok) throw new Error('Failed to fetch status');
      const data = await res.json();
      
      // Update Scheduler Pause State
      const scheduler = data.scheduler || {};
      if (scheduler.paused) {
        statusDot.className = 'status-dot paused';
        statusLabel.innerText = 'Scheduler: Paused';
      } else {
        statusDot.className = 'status-dot active';
        statusLabel.innerText = `Scheduler: ${scheduler.status || 'Active'}`;
      }
      
      // Update Stats
      const stats = data.stats || {};
      queueDepth.innerText = stats.pending_posts ?? '0';
      lastRun.innerText = scheduler.last_run ? formatTimestamp(scheduler.last_run) : 'Never';
    } catch (err) {
      console.error(err);
    }
  }

  function startStatusPolling() {
    fetchStatus();
    pollIntervalId = setInterval(fetchStatus, 5000);
  }

  // ── Pause / Resume Scheduler ─────────────────────────────────────────
  btnPause.addEventListener('click', async () => {
    try {
      const res = await fetch('/api/pause', { method: 'POST' });
      if (!res.ok) throw new Error('API failed');
      const data = await res.json();
      showToast(data.message, 'info');
      fetchStatus();
    } catch (err) {
      showToast('Failed to toggle scheduler state', 'error');
    }
  });

  // Run Pipeline Now
  btnRunNow.addEventListener('click', async () => {
    try {
      const res = await fetch('/api/run', { method: 'POST' });
      const data = await res.json();
      if (data.status === 'ok') {
        showToast(data.message, 'success');
        fetchStatus();
      } else {
        showToast(data.message, 'warning');
      }
    } catch (err) {
      showToast('Failed to trigger run cycle', 'error');
    }
  });

  // ── Dashboard: Post History ──────────────────────────────────────────
  async function fetchHistory() {
    try {
      const res = await fetch('/api/history');
      if (!res.ok) throw new Error('Failed to fetch history');
      const history = await res.json();
      
      let filteredHistory = history;
      if (historyPlatformFilter && historyPlatformFilter.value !== 'all') {
        filteredHistory = history.filter(post => (post.platform || 'x').toLowerCase() === historyPlatformFilter.value);
      }
      
      if (filteredHistory.length === 0) {
        historyGrid.innerHTML = `
          <div class="empty-state">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.3"><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 9h18"/><path d="M9 21V9"/></svg>
            <p>No posts in history matching this filter.</p>
          </div>`;
        return;
      }
      
      historyGrid.innerHTML = filteredHistory.map(post => {
        let statusClass = 'status-pending';
        let statusIcon = '⏳';
        
        if (post.status === 'success') {
          statusClass = 'status-success';
          statusIcon = '✓';
        } else if (post.status === 'failed') {
          statusClass = 'status-failed';
          statusIcon = '✗';
        } else if (['downloading', 'transcoding', 'uploading'].includes(post.status)) {
          statusClass = 'status-running';
          statusIcon = '⚙';
        }
        
        const hasErrorClass = post.status === 'failed' && post.error_msg ? 'has-error' : '';
        const errorDataAttr = post.status === 'failed' && post.error_msg ? `data-error="Error: ${escapeHtml(post.error_msg)}"` : '';
        const errorMsgBanner = post.status === 'failed' && post.error_msg
          ? `<div class="post-error-banner" style="margin-top: 0.5rem; padding: 0.4rem 0.6rem; background: rgba(239, 68, 68, 0.15); border-left: 3px solid #ef4444; border-radius: 4px; font-size: 0.75rem; color: #f87171; overflow-wrap: anywhere;">
              <strong>Error:</strong> ${escapeHtml(post.error_msg)}
             </div>`
          : '';
        
        const platform = post.platform || 'x';
        const displayPlatform = platform.toUpperCase();
        const postUrl = post.external_id || (post.tweet_id ? `https://x.com/i/status/${post.tweet_id}` : null);
        const postLink = postUrl 
          ? `<a href="${postUrl}" target="_blank" class="post-link-generic post-link-${platform}">
              View on ${displayPlatform}
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
             </a>`
          : `<span class="post-link-generic" style="color: var(--text-dark); opacity: 0.5;">No post</span>`;

        const retryBtn = post.status === 'failed' 
          ? `<button class="btn-ghost btn-retry-post" data-id="${post.id}" style="color: var(--accent); padding: 0.2rem 0.5rem; font-size: 0.75rem; border: 1px solid var(--accent); border-radius: 4px; margin-left: 0.5rem;" title="Retry failed post">
              🔄 Retry
             </button>` 
          : '';

        return `
          <div class="post-card ${hasErrorClass}" ${errorDataAttr}>
            <div class="post-card-header">
              <span class="post-title" title="${escapeHtml(post.title || post.video_id)}">${escapeHtml(post.title || post.video_id)}</span>
              <div style="display: flex; gap: 0.5rem; align-items: center; flex-shrink: 0;">
                <span class="item-badge-platform platform-${platform}">${platform}</span>
                <div class="post-status-icon ${statusClass}" title="Status: ${post.status}">
                  ${statusIcon}
                </div>
              </div>
            </div>
            <p class="post-caption">${escapeHtml(post.caption || 'No caption generated')}</p>
            ${errorMsgBanner}
            <div class="post-card-stats-row" style="display: flex; gap: 0.75rem; margin-top: 0.5rem; font-size: 0.8rem; color: var(--text-muted); opacity: 0.85;">
              <span title="Views">👁️ ${post.views || 0}</span>
              <span title="Likes">❤️ ${post.likes || 0}</span>
              <span title="Shares">🔗 ${post.shares || 0}</span>
              <span title="Comments">💬 ${post.comments || 0}</span>
            </div>
            <div class="post-card-footer" style="margin-top: 0.75rem; display: flex; justify-content: space-between; align-items: center;">
              <div>
                <span class="post-acct">${post.account_label ? '@' + escapeHtml(post.account_label.replace('@', '')) : 'Auto'}</span>
                <span style="margin-left: 0.5rem;">${formatTimestamp(post.posted_at || post.created_at)}</span>
              </div>
              <div style="display: flex; gap: 0.5rem; align-items: center;">
                ${retryBtn}
                ${postLink}
              </div>
            </div>
          </div>
        `;
      }).join('');

      // Hook up Retry buttons
      document.querySelectorAll('.btn-retry-post').forEach(btn => {
        btn.addEventListener('click', async () => {
          const id = btn.getAttribute('data-id');
          try {
            const res = await fetch(`/api/posts/${id}/retry`, { method: 'POST' });
            if (!res.ok) throw new Error();
            showToast(`Post #${id} queued for retry!`, 'success');
            fetchHistory();
          } catch {
            showToast('Failed to retry post', 'error');
          }
        });
      });
      
    } catch (err) {
      showToast('Failed to fetch post history', 'error');
    }
  }
  
  btnRefreshHistory.addEventListener('click', fetchHistory);

  // ── Sources: Manage Content Feeds ────────────────────────────────────
  async function fetchSources() {
    try {
      const res = await fetch('/api/sources');
      if (!res.ok) throw new Error('Failed to fetch sources');
      const sources = await res.json();
      
      if (sources.length === 0) {
        sourcesList.innerHTML = `
          <div class="empty-state">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.3"><path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/></svg>
            <p>No sources configured yet.</p>
          </div>`;
        return;
      }
      
      sourcesList.innerHTML = sources.map(source => {
        const isChecked = source.is_active ? 'checked' : '';
        const targets = (source.target_platforms || 'x').split(',').map(t => t.trim().toLowerCase()).filter(Boolean);
        const targetBadges = targets.map(t => `<span class="item-badge-platform platform-${t}" style="font-size: 0.65rem; margin-left: 0.25rem;">➜ ${t.toUpperCase()}</span>`).join('');
        return `
          <div class="list-item">
            <div class="item-meta">
              <div class="item-title">
                <span>${escapeHtml(source.name || 'Unnamed Source')}</span>
                <span class="item-badge-platform">${source.platform}</span>
                ${targetBadges}
              </div>
              <div class="item-subtitle">${escapeHtml(source.url)}</div>
            </div>
            <div class="item-actions">
              <label class="toggle-switch sm">
                <input type="checkbox" class="source-toggle" data-id="${source.id}" ${isChecked} />
                <span class="toggle-slider"></span>
              </label>
              <button class="btn-danger-ghost delete-source-btn" data-id="${source.id}" title="Remove source">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
              </button>
            </div>
          </div>
        `;
      }).join('');
      
      // Hook up toggle switches
      document.querySelectorAll('.source-toggle').forEach(el => {
        el.addEventListener('change', async (e) => {
          const id = e.target.getAttribute('data-id');
          const active = e.target.checked;
          try {
            const res = await fetch(`/api/sources/${id}/toggle`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ active })
            });
            if (!res.ok) throw new Error();
            showToast(active ? 'Source activated' : 'Source deactivated', 'success');
            fetchStatus();
          } catch (err) {
            e.target.checked = !active;
            showToast('Failed to toggle source state', 'error');
          }
        });
      });
      
      // Hook up delete buttons
      document.querySelectorAll('.delete-source-btn').forEach(el => {
        el.addEventListener('click', async (e) => {
          const btn = e.target.closest('.delete-source-btn');
          const id = btn.getAttribute('data-id');
          if (!confirm('Are you sure you want to remove this source?')) return;
          
          try {
            const res = await fetch(`/api/sources/${id}`, { method: 'DELETE' });
            if (!res.ok) throw new Error();
            showToast('Source removed successfully', 'success');
            fetchSources();
            fetchStatus();
          } catch (err) {
            showToast('Failed to delete source', 'error');
          }
        });
      });
      
    } catch (err) {
      showToast('Failed to load sources', 'error');
    }
  }
  
  formAddSource.addEventListener('submit', async (e) => {
    e.preventDefault();
    const url = sourceUrl.value.trim();
    const platform = sourcePlatform.value;
    const name = sourceName.value.trim();
    
    if (!url) return;
    
    try {
      new URL(url);
    } catch (_) {
      showToast('Please enter a valid absolute URL (starting with http:// or https://)', 'warning');
      return;
    }
    
    // Gather destinations checkboxes
    const destinations = Array.from(document.querySelectorAll('input[name="destinations"]:checked')).map(cb => cb.value);
    if (destinations.length === 0) {
      showToast('Please select at least one target platform destination', 'warning');
      return;
    }
    
    try {
      const res = await fetch('/api/sources', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, platform, name, target_platforms: destinations.join(',') })
      });
      
      if (res.status === 409) {
        showToast('Source URL already exists', 'warning');
        return;
      }
      
      if (!res.ok) throw new Error();
      
      showToast('Source added successfully', 'success');
      sourceUrl.value = '';
      sourceName.value = '';
      fetchSources();
      fetchStatus();
    } catch (err) {
      showToast('Failed to add source', 'error');
    }
  });

  let allAccounts = [];

  // Update Quick Post Accounts select field dynamically based on selected platform
  function updateQuickPostAccounts() {
    if (!qpAccount) return;
    const selectedPlatform = qpPlatform ? qpPlatform.value : 'x';
    const filtered = allAccounts.filter(acct => acct.platform === selectedPlatform);
    qpAccount.innerHTML = '<option value="">Auto-Select (Rotate)</option>' + 
      filtered.map(acct => `<option value="${acct.label}">${acct.label}</option>`).join('');
  }

  if (qpPlatform) {
    qpPlatform.addEventListener('change', updateQuickPostAccounts);
  }

  // ── Accounts: Multi-Platform OAuth & Cookie Logins ─────────────────────
  async function fetchAccounts() {
    try {
      const res = await fetch('/api/accounts');
      if (!res.ok) throw new Error('Failed to fetch accounts');
      allAccounts = await res.json();
      
      // Populate/filter Quick Post dropdown
      updateQuickPostAccounts();
      
      if (allAccounts.length === 0) {
        accountsList.innerHTML = `
          <div class="empty-state">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.3"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
            <p>No accounts linked yet.</p>
          </div>`;
        return;
      }
      
      // Fetch account health summary
      let healthMap = {};
      try {
        const hRes = await fetch('/api/accounts/health');
        if (hRes.ok) {
          const hData = await hRes.json();
          hData.forEach(item => {
            healthMap[`${item.platform}_${item.label}`] = item;
          });
        }
      } catch (hErr) {
        console.warn('Failed to fetch account health summary:', hErr);
      }

      accountsList.innerHTML = allAccounts.map(acct => {
        const platform = acct.platform || 'x';
        const limit = platform === 'youtube' ? 8 : (platform === 'x' ? 50 : (platform === 'instagram' ? 10 : 20));
        const health = healthMap[`${platform}_${acct.label}`] || { health_status: 'healthy', health_reason: 'Operational' };
        
        let healthBadgeClass = 'badge-approval';
        let healthColor = '#10b981';
        if (health.health_status === 'warning') {
          healthColor = '#f59e0b';
        } else if (health.health_status === 'challenged') {
          healthColor = '#ef4444';
        }

        return `
          <div class="list-item">
            <div class="item-meta">
              <div class="item-title">
                <span>${escapeHtml(acct.label || 'Unknown')}</span>
                <span class="item-badge-platform platform-${platform}">${platform.toUpperCase()}</span>
                <span class="item-badge-platform">${acct.auth_mode || 'api'}</span>
                <span style="font-size: 0.75rem; font-weight: 600; padding: 0.15rem 0.5rem; border-radius: 4px; background: rgba(255,255,255,0.05); color: ${healthColor}; border: 1px solid ${healthColor}40;" title="${escapeHtml(health.health_reason)}">
                  ● ${escapeHtml(health.health_status.toUpperCase())} (${escapeHtml(health.health_reason)})
                </span>
              </div>
              <div class="item-stats">
                <span>Posts today: <strong>${acct.post_count_today || 0} / ${limit}</strong></span>
                <span>Last used: <strong>${acct.last_used_at ? formatTimestamp(acct.last_used_at) : 'Never'}</strong></span>
              </div>
            </div>
            <div class="item-actions">
              <button class="btn-danger-ghost delete-acct-btn" data-label="${acct.label}" data-platform="${platform}" title="Delete account link">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
              </button>
            </div>
          </div>
        `;
      }).join('');
      
      // Hook up delete buttons
      document.querySelectorAll('.delete-acct-btn').forEach(el => {
        el.addEventListener('click', async (e) => {
          const btn = e.target.closest('.delete-acct-btn');
          const label = btn.getAttribute('data-label');
          const platform = btn.getAttribute('data-platform');
          if (!confirm(`Are you sure you want to remove ${platform.toUpperCase()} account ${label}?`)) return;
          
          try {
            const res = await fetch(`/api/accounts/${encodeURIComponent(label)}?platform=${platform}`, { method: 'DELETE' });
            if (!res.ok) throw new Error();
            showToast('Account removed successfully', 'success');
            fetchAccounts();
            fetchStatus();
          } catch (err) {
            showToast('Failed to delete account', 'error');
          }
        });
      });
      
    } catch (err) {
      showToast('Failed to load accounts', 'error');
    }
  }
  
  // Toggle fields based on selected Platform and Auth Mode
  function toggleAccountFields() {
    const platform = acctPlatform ? acctPlatform.value : 'x';
    const authMode = acctAuthMode ? acctAuthMode.value : 'api';
    
    // Hide all first
    if (acctApiFields) acctApiFields.style.display = 'none';
    if (acctCookieFields) acctCookieFields.style.display = 'none';
    if (acctInstagramApiFields) acctInstagramApiFields.style.display = 'none';
    if (acctInstagramCookieFields) acctInstagramCookieFields.style.display = 'none';
    if (acctTikTokApiFields) acctTikTokApiFields.style.display = 'none';
    if (acctTikTokCookieFields) acctTikTokCookieFields.style.display = 'none';
    if (acctYoutubeOauthFields) acctYoutubeOauthFields.style.display = 'none';
    
    if (platform === 'x') {
      if (authMode === 'api') {
        if (acctApiFields) acctApiFields.style.display = 'flex';
      } else {
        if (acctCookieFields) acctCookieFields.style.display = 'flex';
      }
    } else if (platform === 'instagram') {
      if (authMode === 'api') {
        if (acctInstagramApiFields) acctInstagramApiFields.style.display = 'flex';
      } else {
        if (acctInstagramCookieFields) acctInstagramCookieFields.style.display = 'flex';
      }
    } else if (platform === 'tiktok') {
      if (authMode === 'api') {
        if (acctTikTokApiFields) acctTikTokApiFields.style.display = 'flex';
      } else {
        if (acctTikTokCookieFields) acctTikTokCookieFields.style.display = 'flex';
      }
    } else if (platform === 'youtube') {
      if (acctYoutubeOauthFields) acctYoutubeOauthFields.style.display = 'flex';
    }
  }

  if (acctPlatform) {
    acctPlatform.addEventListener('change', toggleAccountFields);
  }
  if (acctAuthMode) {
    acctAuthMode.addEventListener('change', toggleAccountFields);
  }

  formAddAccount.addEventListener('submit', async (e) => {
    e.preventDefault();
    const label = acctLabel.value.trim();
    const platform = acctPlatform.value;
    const auth_mode = acctAuthMode.value;
    const proxy_url = document.getElementById('acct-proxy-url').value.trim() || null;
    const user_agent = document.getElementById('acct-user-agent').value.trim() || null;
    
    if (!label) return;
    
    if (!label.startsWith('@')) {
      label = '@' + label;
    }
    
    if (platform === 'x') {
      const handleRegex = /^@[a-zA-Z0-9_]{1,15}$/;
      if (!handleRegex.test(label)) {
        showToast('X handle can only contain letters, numbers, and underscores, up to 15 characters (excluding @)', 'warning');
        return;
      }
    }
    
    let credentials = {};
    
    if (platform === 'x') {
      if (auth_mode === 'api') {
        const api_key = acctApiKey.value.trim();
        const api_secret = acctApiSecret.value.trim();
        const access_token = acctAccessToken.value.trim();
        const access_token_secret = acctAccessSecret.value.trim();
        if (!api_key || !api_secret || !access_token || !access_token_secret) {
          showToast('Please fill out all API fields for X', 'warning');
          return;
        }
        credentials = { api_key, api_secret, access_token, access_token_secret };
      } else {
        const cookie_auth_token = acctCookieAuthToken.value.trim();
        const cookie_ct0 = acctCookieCt0.value.trim();
        if (!cookie_auth_token) {
          showToast('auth_token cookie is required for X Cookie mode', 'warning');
          return;
        }
        credentials = { cookie_auth_token, cookie_ct0 };
      }
    } else if (platform === 'instagram') {
      if (auth_mode === 'api') {
        const access_token = acctIgAccessToken.value.trim();
        const instagram_account_id = acctIgAccountId.value.trim();
        if (!access_token || !instagram_account_id) {
          showToast('Please fill out all Graph API fields for Instagram', 'warning');
          return;
        }
        credentials = { access_token, instagram_account_id };
      } else {
        const username = acctIgUsername.value.trim();
        const password = acctIgPassword.value.trim();
        if (!username || !password) {
          showToast('Instagram Username and Password are required for Cookie mode', 'warning');
          return;
        }
        credentials = { username, password };
      }
    } else if (platform === 'tiktok') {
      if (auth_mode === 'api') {
        const access_token = acctTtAccessToken.value.trim();
        const open_id = acctTtOpenId.value.trim();
        if (!access_token || !open_id) {
          showToast('TikTok Access Token and Open ID are required for API mode', 'warning');
          return;
        }
        credentials = { access_token, open_id };
      } else {
        const session_id = acctTtSessionId.value.trim();
        const tt_user_agent = acctTtUserAgent.value.trim();
        if (!session_id) {
          showToast('TikTok sessionid Cookie is required for Cookie mode', 'warning');
          return;
        }
        credentials = { session_id, user_agent: tt_user_agent };
      }
    } else if (platform === 'youtube') {
      const client_id = acctYtClientId.value.trim();
      const client_secret = acctYtClientSecret.value.trim();
      const refresh_token = acctYtRefreshToken.value.trim();
      if (!client_id || !client_secret || !refresh_token) {
        showToast('Client ID, Client Secret, and Refresh Token are required for YouTube OAuth', 'warning');
        return;
      }
      credentials = { client_id, client_secret, refresh_token };
    }
    
    const payload = { label, platform, auth_mode, credentials, proxy_url, user_agent };
    
    try {
      const res = await fetch('/api/accounts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      
      if (res.status === 409) {
        showToast(`Account label already exists on ${platform}`, 'warning');
        return;
      }
      
      if (!res.ok) throw new Error();
      
      showToast(`${platform.toUpperCase()} Account linked successfully`, 'success');
      
      // Reset inputs
      document.getElementById('acct-proxy-url').value = '';
      document.getElementById('acct-user-agent').value = '';
      acctLabel.value = '';
      acctApiKey.value = '';
      acctApiSecret.value = '';
      acctAccessToken.value = '';
      acctAccessSecret.value = '';
      acctCookieAuthToken.value = '';
      acctCookieCt0.value = '';
      acctIgAccessToken.value = '';
      acctIgAccountId.value = '';
      acctIgUsername.value = '';
      acctIgPassword.value = '';
      acctTtAccessToken.value = '';
      acctTtOpenId.value = '';
      acctTtSessionId.value = '';
      acctTtUserAgent.value = '';
      acctYtClientId.value = '';
      acctYtClientSecret.value = '';
      acctYtRefreshToken.value = '';
      
      fetchAccounts();
      fetchStatus();
    } catch (err) {
      showToast('Failed to link account', 'error');
    }
  });
  // ── Settings: Configuration Form ─────────────────────────────────────
  // Sync slider label
  settingInterval.addEventListener('input', (e) => {
    intervalValue.innerText = e.target.value;
  });

  async function fetchSettings() {
    try {
      const res = await fetch('/api/settings');
      if (!res.ok) throw new Error();
      const settings = await res.json();

      settingInterval.value = settings.interval_minutes || 30;
      intervalValue.innerText = settingInterval.value;
      
      settingCaptionAi.checked = settings.caption_ai === 'true' || settings.caption_ai === true;
      settingGeminiKey.value = settings.gemini_api_key || '';
      settingTemplate.value = settings.caption_template || '';
      
      // Load Sprint 6 variables
      const settingJitter = document.getElementById('setting-scheduler-jitter');
      if (settingJitter) {
        settingJitter.checked = settings.enable_scheduler_jitter !== 'false' && settings.enable_scheduler_jitter !== false;
      }
      const settingVerticalPad = document.getElementById('setting-vertical-pad');
      if (settingVerticalPad) {
        settingVerticalPad.value = settings.vertical_pad_mode || 'blur_background';
      }
      const settingXLink = document.getElementById('setting-x-link-placement');
      if (settingXLink) {
        settingXLink.value = settings.x_link_placement || 'thread_reply';
      }
      const settingWebhook = document.getElementById('setting-webhook-url');
      if (settingWebhook) {
        settingWebhook.value = settings.webhook_url || '';
      }
      const settingTgToken = document.getElementById('setting-telegram-token');
      if (settingTgToken) {
        settingTgToken.value = settings.telegram_bot_token || '';
      }
      const settingTgChat = document.getElementById('setting-telegram-chat');
      if (settingTgChat) {
        settingTgChat.value = settings.telegram_chat_id || '';
      }
      const settingTplX = document.getElementById('setting-template-x');
      if (settingTplX) {
        settingTplX.value = settings.caption_template_x || '';
      }
      const settingTplIg = document.getElementById('setting-template-ig');
      if (settingTplIg) {
        settingTplIg.value = settings.caption_template_instagram || '';
      }
      const settingTplTt = document.getElementById('setting-template-tt');
      if (settingTplTt) {
        settingTplTt.value = settings.caption_template_tiktok || '';
      }
      const settingTplYt = document.getElementById('setting-template-yt');
      if (settingTplYt) {
        settingTplYt.value = settings.caption_template_youtube || '';
      }
    } catch (err) {
      showToast('Failed to load system settings', 'error');
    }
  }

  formSettings.addEventListener('submit', async (e) => {
    e.preventDefault();
    const interval_minutes = settingInterval.value;
    const caption_ai = settingCaptionAi.checked;
    const gemini_api_key = settingGeminiKey.value.trim();
    
    // Read Sprint 6 & Feature 1-4 fields
    const enable_scheduler_jitter = document.getElementById('setting-scheduler-jitter')?.checked ?? true;
    const vertical_pad_mode = document.getElementById('setting-vertical-pad')?.value ?? 'blur_background';
    const x_link_placement = document.getElementById('setting-x-link-placement')?.value ?? 'thread_reply';
    const webhook_url = document.getElementById('setting-webhook-url')?.value.trim() ?? '';
    const telegram_bot_token = document.getElementById('setting-telegram-token')?.value.trim() ?? '';
    const telegram_chat_id = document.getElementById('setting-telegram-chat')?.value.trim() ?? '';
    const caption_template_x = document.getElementById('setting-template-x')?.value.trim() ?? '';
    const caption_template_instagram = document.getElementById('setting-template-ig')?.value.trim() ?? '';
    const caption_template_tiktok = document.getElementById('setting-template-tt')?.value.trim() ?? '';
    const caption_template_youtube = document.getElementById('setting-template-yt')?.value.trim() ?? '';
    
    const body = {
      interval_minutes,
      caption_ai,
      gemini_api_key,
      enable_scheduler_jitter,
      vertical_pad_mode,
      x_link_placement,
      webhook_url,
      telegram_bot_token,
      telegram_chat_id,
      caption_template_x,
      caption_template_instagram,
      caption_template_tiktok,
      caption_template_youtube
    };
    
    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      
      if (!res.ok) throw new Error();
      showToast('Settings saved successfully', 'success');
      fetchSettings();
    } catch (err) {
      showToast('Failed to save settings', 'error');
    }
  });

  // Bind Export System Backup trigger
  const btnExportBackup = document.getElementById('btn-export-backup');
  if (btnExportBackup) {
    btnExportBackup.addEventListener('click', () => {
      showToast('Generating system backup package...', 'info');
      window.location.href = '/api/system/backup';
    });
  }

  // Bind Webhook test payload trigger
  const btnTestWebhook = document.getElementById('btn-test-webhook');
  if (btnTestWebhook) {
    btnTestWebhook.addEventListener('click', async () => {
      try {
        const res = await fetch('/api/test-webhook', { method: 'POST' });
        if (!res.ok) throw new Error();
        const data = await res.json();
        showToast(data.message, 'success');
      } catch (err) {
        showToast('Failed to trigger test webhook. Verify settings and URL.', 'error');
      }
    });
  }

  // ── Logs: Terminal log streaming ─────────────────────────────────────
  async function fetchLogs() {
    try {
      const res = await fetch('/api/logs');
      if (!res.ok) throw new Error('Failed to load logs');
      const logs = await res.json();
      
      const filteredLogs = logs.filter(log => {
        // Filter by level
        if (logFilterError && logFilterError.checked && log.level !== 'ERROR') {
          return false;
        }
        
        const messageLower = (log.message || "").toLowerCase();
        const levelLower = (log.level || "").toLowerCase();
        
        const isSchedulerRelated = messageLower.includes("scheduler");
        const isDownloaderRelated = messageLower.includes("downloader") || messageLower.includes("download") || messageLower.includes("transcod") || messageLower.includes("ffmpeg") || messageLower.includes("yt-dlp");
        const isPublisherRelated = messageLower.includes("publisher") || messageLower.includes("tweet") || messageLower.includes("post") || messageLower.includes("twikit") || messageLower.includes("tweepy") || messageLower.includes("upload");
        
        if (logFilterScheduler && !logFilterScheduler.checked && isSchedulerRelated) return false;
        if (logFilterDownloader && !logFilterDownloader.checked && isDownloaderRelated) return false;
        if (logFilterPublisher && !logFilterPublisher.checked && isPublisherRelated) return false;
        
        return true;
      });
      
      logContent.innerHTML = filteredLogs.map(log => {
        let levelClass = 'log-level-info';
        if (log.level === 'WARN' || log.level === 'WARNING') levelClass = 'log-level-warn';
        if (log.level === 'ERROR') levelClass = 'log-level-error';
        
        // Format ISO timestamp to hh:mm:ss
        let timeStr = log.timestamp;
        try {
          let rawTimestamp = log.timestamp;
          if (rawTimestamp && !rawTimestamp.includes('Z') && !rawTimestamp.includes('+')) {
            rawTimestamp = rawTimestamp.replace(' ', 'T') + 'Z';
          }
          const date = new Date(rawTimestamp);
          if (!isNaN(date.getTime())) {
            timeStr = date.toLocaleTimeString([], { hour12: false });
          }
        } catch(e) {}

        return `
          <div class="log-line">
            <span class="log-time">[${timeStr}]</span>
            <span class="${levelClass}">${log.level}</span>: 
            <span class="log-message">${escapeHtml(log.message)}</span>
          </div>
        `;
      }).join('');
      
      if (logAutoscroll.checked) {
        logTerminal.scrollTop = logTerminal.scrollHeight;
      }
    } catch (err) {
      // Don't show toast inside fast polling interval to avoid annoying user
      console.error(err);
    }
  }

  function escapeHtml(text) {
    if (!text) return '';
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  btnClearLogs.addEventListener('click', () => {
    logContent.innerHTML = '';
    showToast('Log screen cleared (database logs intact)', 'info');
  });

  function startLogPolling() {
    stopLogPolling();
    fetchLogs();
    logIntervalId = setInterval(fetchLogs, 3000);
  }

  function stopLogPolling() {
    if (logIntervalId) {
      clearInterval(logIntervalId);
      logIntervalId = null;
    }
  }

  // ── Initialization ───────────────────────────────────────────────────
  // Create toast container element dynamically if not present
  if (!document.getElementById('toast-container')) {
    const toastContainer = document.createElement('div');
    toastContainer.id = 'toast-container';
    document.body.appendChild(toastContainer);
  }
  
  // Initialize dynamic account form fields
  toggleAccountFields();
  
  // Hook up Quick Post form listener (Task 3.3)
  if (formQuickPost) {
    formQuickPost.addEventListener('submit', async (e) => {
      e.preventDefault();
      const url = qpUrl.value.trim();
      const caption = qpCaption.value.trim();
      const account = qpAccount.value;
      const platform = qpPlatform.value;
      
      if (!url) return;
      
      try {
        new URL(url);
      } catch (_) {
        showToast('Please enter a valid absolute URL (starting with http:// or https://)', 'warning');
        return;
      }
      
      qpSubmitBtn.disabled = true;
      qpSubmitBtn.innerHTML = '⚙ Processing...';
      
      try {
        const res = await fetch('/api/quick-post', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url, caption, account, platform })
        });
        
        if (!res.ok) throw new Error();
        
        showToast('Quick Post pipeline initiated in background!', 'success');
        qpUrl.value = '';
        qpCaption.value = '';
        fetchHistory(); // Refresh history immediately
      } catch (err) {
        showToast('Failed to initiate Quick Post', 'error');
      } finally {
        qpSubmitBtn.disabled = false;
        qpSubmitBtn.innerHTML = `
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
          Publish Now`;
      }
    });
  }

  // ── Multi-Format Ingestion & Approval Queue Logic ──
  const dropZone = document.getElementById('drop-zone');
  const fileInput = document.getElementById('file-input');
  const fileUploadStatus = document.getElementById('file-upload-status');
  const formIngest = document.getElementById('form-ingest');
  const ingestMediaPath = document.getElementById('ingest-media-path');
  const ingestMediaType = document.getElementById('ingest-media-type');
  const ingestText = document.getElementById('ingest-text');
  const ingestAccount = document.getElementById('ingest-account');
  const btnSaveDraft = document.getElementById('btn-save-draft');
  const btnApproveAll = document.getElementById('btn-approve-all');
  const btnRefreshApproval = document.getElementById('btn-refresh-approval');
  const approvalGrid = document.getElementById('approval-grid');

  if (dropZone && fileInput) {
    dropZone.addEventListener('click', () => fileInput.click());
    dropZone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropZone.classList.add('dragover');
    });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropZone.classList.remove('dragover');
      if (e.dataTransfer.files && e.dataTransfer.files[0]) {
        handleFileUpload(e.dataTransfer.files[0]);
      }
    });
    fileInput.addEventListener('change', () => {
      if (fileInput.files && fileInput.files[0]) {
        handleFileUpload(fileInput.files[0]);
      }
    });
  }

  async function handleFileUpload(file) {
    if (!fileUploadStatus) return;
    fileUploadStatus.textContent = `Uploading ${file.name}...`;
    const formData = new FormData();
    formData.append('file', file);

    try {
      const res = await fetch('/api/upload', {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Upload failed');

      ingestMediaPath.value = data.file_path;
      ingestMediaType.value = data.media_type;
      fileUploadStatus.textContent = `✓ Uploaded: ${data.filename} (${data.media_type.toUpperCase()})`;
      showToast(`Uploaded ${data.filename}`, 'success');
    } catch (err) {
      fileUploadStatus.textContent = `❌ Upload failed: ${err.message}`;
      showToast(err.message, 'error');
    }
  }

  async function submitIngest(requiresApproval = true) {
    const text = ingestText ? ingestText.value.trim() : '';
    const mediaPath = ingestMediaPath ? ingestMediaPath.value : '';
    let mediaType = ingestMediaType ? ingestMediaType.value : 'text';
    const scheduledAtInput = document.getElementById('ingest-scheduled-at');
    const scheduled_at = scheduledAtInput ? scheduledAtInput.value : '';

    if (mediaPath && mediaType === 'text') {
      mediaType = 'video';
    }

    const platformCbs = document.querySelectorAll('input[name="ingest-platform"]:checked');
    const targetPlatforms = Array.from(platformCbs).map(cb => cb.value);

    if (!text && !mediaPath) {
      showToast('Please upload a file or write post content', 'error');
      return;
    }

    try {
      const res = await fetch('/api/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content_type: mediaType,
          text: text,
          media_path: mediaPath,
          target_platforms: targetPlatforms,
          account: ingestAccount ? ingestAccount.value : '',
          requires_approval: requiresApproval,
          scheduled_at: scheduled_at
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Ingest failed');

      showToast(requiresApproval ? 'Added to Approval Queue!' : 'Submitted & Approved!', 'success');
      
      // Reset form
      if (ingestText) ingestText.value = '';
      if (ingestMediaPath) ingestMediaPath.value = '';
      if (ingestMediaType) ingestMediaType.value = 'text';
      if (fileUploadStatus) fileUploadStatus.textContent = '';
      if (scheduledAtInput) scheduledAtInput.value = '';
      
      if (activeTab === 'approval') fetchApprovalQueue();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  if (formIngest) {
    formIngest.addEventListener('submit', (e) => {
      e.preventDefault();
      submitIngest(true);
    });
  }

  if (btnSaveDraft) {
    btnSaveDraft.addEventListener('click', () => submitIngest(true));
  }

  async function fetchApprovalQueue() {
    if (!approvalGrid) return;
    try {
      const res = await fetch('/api/approval-queue');
      const items = await res.json();
      renderApprovalQueue(items);
    } catch (err) {
      console.error('Failed to fetch approval queue:', err);
    }
  }

  function renderApprovalQueue(items) {
    if (!approvalGrid) return;
    if (!items || items.length === 0) {
      approvalGrid.innerHTML = `
        <div class="empty-state">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.3"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg>
          <p>No draft posts waiting for approval</p>
        </div>`;
      return;
    }

    approvalGrid.innerHTML = items.map(item => `
      <div class="approval-card" id="app-card-${item.id}">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span class="badge-approval">${item.status.toUpperCase()}</span>
          <div style="display: flex; gap: 0.4rem; align-items: center;">
            ${item.scheduled_at ? `<span style="font-size: 0.75rem; color: #a7f3d0; background: rgba(16,185,129,0.15); padding: 0.15rem 0.4rem; border-radius: 4px;">📅 ${escapeHtml(item.scheduled_at)}</span>` : ''}
            <span style="font-size: 0.8rem; color: var(--text-muted);">${item.platform.toUpperCase()}</span>
          </div>
        </div>
        <div style="font-weight: 500; font-size: 0.95rem; line-height: 1.4;">
          ${escapeHtml(item.caption || item.title || 'Untitled Draft')}
        </div>
        ${item.media_path ? `<div style="font-size: 0.8rem; color: var(--accent);">📁 ${escapeHtml(item.media_path)}</div>` : ''}
        <div style="display: flex; gap: 0.5rem; margin-top: 0.5rem; justify-content: flex-end;">
          <button class="btn-ghost btn-reject" data-id="${item.id}" style="color: var(--color-danger); padding: 0.3rem 0.6rem; font-size: 0.8rem;">Reject</button>
          <button class="btn-accent btn-approve" data-id="${item.id}" style="padding: 0.3rem 0.75rem; font-size: 0.8rem;">Approve</button>
        </div>
      </div>
    `).join('');

    // Attach approve / reject listeners
    document.querySelectorAll('.btn-approve').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.getAttribute('data-id');
        try {
          const res = await fetch(`/api/posts/${id}/approve`, { method: 'POST' });
          if (!res.ok) throw new Error();
          showToast(`Post #${id} approved!`, 'success');
          fetchApprovalQueue();
        } catch {
          showToast('Failed to approve post', 'error');
        }
      });
    });

    document.querySelectorAll('.btn-reject').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.getAttribute('data-id');
        try {
          const res = await fetch(`/api/posts/${id}`, { method: 'DELETE' });
          if (!res.ok) throw new Error();
          showToast(`Post #${id} rejected`, 'info');
          fetchApprovalQueue();
        } catch {
          showToast('Failed to reject post', 'error');
        }
      });
    });
  }

  if (btnApproveAll) {
    btnApproveAll.addEventListener('click', async () => {
      try {
        const res = await fetch('/api/posts/approve-all', { method: 'POST' });
        const data = await res.json();
        showToast(`Approved ${data.approved_count} post(s)!`, 'success');
        fetchApprovalQueue();
      } catch {
        showToast('Failed to batch approve', 'error');
      }
    });
  }

  if (btnRefreshApproval) {
    btnRefreshApproval.addEventListener('click', fetchApprovalQueue);
  }

  // Hook tab switch logic to refresh approval queue when tab opens
  const origSwitchTab = switchTab;
  switchTab = function(tabName) {
    origSwitchTab(tabName);
    if (tabName === 'approval') fetchApprovalQueue();
  };

  // Hook up history platform filter
  if (historyPlatformFilter) {
    historyPlatformFilter.addEventListener('change', fetchHistory);
  }

  // Hook up Log filters listeners (Task 3.5)
  [logFilterScheduler, logFilterDownloader, logFilterPublisher, logFilterError].forEach(cb => {
    if (cb) {
      cb.addEventListener('change', fetchLogs);
    }
  });

  // Start status polling
  startStatusPolling();
  
  // Initial active tab load
  switchTab(activeTab);
});
