// ==================== Tab Switching ====================
document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', function(e) {
        e.preventDefault();
        const tab = this.dataset.tab;

        // Update nav
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        this.classList.add('active');

        // Update content
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        document.getElementById('tab-' + tab).classList.add('active');

        // Load data for tab
        if (tab === 'overview') loadOverview();
        if (tab === 'users') loadUsers();
        if (tab === 'relay') loadOcCatalog();
        if (tab === 'tokens') loadTokenStats();
        if (tab === 'logs') loadLogs();
    });
});

// ==================== Utility Functions ====================
function esc(s) {
    if (s == null) return '';
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

function uiLog(m) {
    fetch('/api/ui_log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: String(m) })
    }).catch(() => {});
}

function userAlert(m) {
    uiLog(m);
    window.alert(m);
}

function copyText(t) {
    if (!t) return;
    navigator.clipboard.writeText(t)
        .then(() => userAlert('已复制'))
        .catch(() => {
            var ta = document.createElement('textarea');
            ta.value = t;
            ta.style.position = 'fixed';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            userAlert('已复制');
        });
}

function formatTime(t) {
    if (!t) return '--';
    return t;
}

function formatNumber(n) {
    if (n == null) return '0';
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return String(n);
}

// ==================== Overview ====================
function loadOverview() {
    fetch('/api/status')
        .then(r => r.json())
        .then(d => {
            document.getElementById('statAccounts').textContent = Object.keys(d.users || {}).length;
            document.getElementById('statNextCheck').textContent = d.next_check || '--';
            // Load OC count
            return fetch('/api/oc_catalog').then(r => r.json());
        })
        .then(d => {
            document.getElementById('statOCs').textContent = d.relay_unique_keys || 0;
        })
        .catch(() => {});

    refreshAccountsHealth();
}

// ==================== Users Management ====================
function loadUsers() {
    refreshAccountsHealth();
}

function renderAccountItem(a, defaultUser) {
    var isDef = String(a.uid) === String(defaultUser);
    var statusBadge = a.ok
        ? '<span class="badge">正常</span>'
        : '<span class="badge danger">异常</span>';
    var defaultBadge = isDef ? ' <span class="badge info">默认</span>' : '';

    return '<div class="account-item">' +
        '<div class="account-info">' +
            '<div class="account-avatar">' + esc((a.name || '?')[0]) + '</div>' +
            '<div>' +
                '<div class="account-name">' + esc(a.name || '未命名') + defaultBadge + '</div>' +
                '<div class="account-id">' + esc(a.userId || '') + '</div>' +
            '</div>' +
        '</div>' +
        '<div>' + statusBadge + '</div>' +
        '<div class="account-actions">' +
            (!isDef ? '<button class="btn-ghost" onclick="setDefault(\'' + esc(a.uid) + '\')">设默认</button>' : '') +
            '<button class="btn-ghost" onclick="fetchAccountTrial(\'' + esc(a.uid) + '\')">查体验</button>' +
            '<button class="btn-ghost" onclick="copyOC(\'' + esc(a.uid) + '\')">复制OC</button>' +
            '<button class="btn-ghost" onclick="refetchOC(\'' + esc(a.uid) + '\')">刷新</button>' +
            '<button class="btn-ghost" style="color:var(--danger)" onclick="deleteUser(\'' + esc(a.uid) + '\')">删除</button>' +
        '</div>' +
    '</div>';
}

function renderUsersTable(accounts, defaultUser) {
    var tbody = document.getElementById('usersTableBody');
    if (!accounts.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-state">还没有账号</td></tr>';
        return;
    }
    tbody.innerHTML = accounts.map(function(a) {
        var isDef = String(a.uid) === String(defaultUser);
        var statusBadge = a.ok
            ? '<span class="badge">正常</span>'
            : '<span class="badge danger">异常</span>';
        var defaultBadge = isDef ? ' <span class="badge info">默认</span>' : '';

        return '<tr>' +
            '<td>' + esc(a.uid) + '</td>' +
            '<td>' + esc(a.name || '未命名') + defaultBadge + '</td>' +
            '<td><code>' + esc(a.userId || '') + '</code></td>' +
            '<td>' + statusBadge + '</td>' +
            '<td id="trial-' + esc(a.uid) + '">--</td>' +
            '<td>' +
                (!isDef ? '<button class="btn-sm" onclick="setDefault(\'' + esc(a.uid) + '\')">设默认</button> ' : '') +
                '<button class="btn-sm" onclick="fetchAccountTrial(\'' + esc(a.uid) + '\')">查体验</button> ' +
                '<button class="btn-sm" onclick="deleteUser(\'' + esc(a.uid) + '\')">删除</button>' +
            '</td>' +
        '</tr>';
    }).join('');
}

function refreshAccountsHealth() {
    return fetch('/api/accounts_health')
        .then(r => r.json())
        .then(function(d) {
            var accounts = d.accounts || [];
            var defaultUser = d.default_user;

            // Update stat
            document.getElementById('statAccounts').textContent = accounts.length;
            document.getElementById('accountCount').textContent = accounts.length;

            // Overview account list
            var list = document.getElementById('accountsList');
            if (!accounts.length) {
                list.innerHTML = '<div class="empty-state">还没有账号，去用户管理导入</div>';
            } else {
                list.innerHTML = accounts.map(a => renderAccountItem(a, defaultUser)).join('');
            }

            // Users table
            renderUsersTable(accounts, defaultUser);

            // Auto fetch trial (with skipAutoRefetch)
            accounts.forEach(function(a) {
                fetchAccountTrial(String(a.uid), true);
            });
        })
        .catch(function(e) {
            console.error('加载失败:', e);
        });
}

// ==================== Account Actions ====================
function fetchAccountTrial(uid, skipAuto) {
    var el = document.getElementById('trial-' + uid);
    if (el) el.textContent = '查询中...';

    return fetch('/api/account_trial', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: uid })
    })
    .then(r => r.json())
    .then(function(d) {
        if (!d.success) {
            if (el) el.innerHTML = '<span class="badge danger">失败</span>';
            return;
        }
        if (d.no_account) {
            if (el) el.innerHTML = '<span class="badge warning">无到期</span>';
            // Auto refetch if not skipped
            if (!skipAuto) {
                uiLog('体验[' + uid + ']: 无到期时间，自动重新申请');
                refetchOC(uid);
            }
            return;
        }
        if (!d.ok) {
            if (el) el.innerHTML = '<span class="badge danger">' + esc(d.message || '错误') + '</span>';
            return;
        }
        if (d.mmss) {
            var rs = d.remain_sec;
            var cls = (rs != null && rs <= 300) ? 'warning' : '';
            if (el) el.innerHTML = '<span class="badge ' + cls + '">' + esc(d.mmss) + '</span>';
            uiLog('体验[' + uid + ']: ' + d.mmss);
        }
    })
    .catch(function() {
        if (el) el.textContent = '--';
    });
}

function setDefault(uid) {
    fetch('/api/set_default_user', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: uid })
    })
    .then(r => r.json())
    .then(function(d) {
        if (d.success) {
            userAlert('已设为默认');
            refreshAccountsHealth();
        } else {
            userAlert(d.error || '失败');
        }
    })
    .catch(e => userAlert(String(e)));
}

function copyOC(uid) {
    fetch('/api/account_copy_line', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: uid })
    })
    .then(r => r.json())
    .then(function(d) {
        if (d.success) copyText(d.line);
        else userAlert(d.error || '失败');
    })
    .catch(e => userAlert(String(e)));
}

function refetchOC(uid) {
    uiLog('开始刷新 OC [' + uid + ']...');
    fetch('/api/claw_refetch_oc', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: uid })
    })
    .then(r => r.json())
    .then(function(d) {
        if (d.success) {
            uiLog('刷新成功 [' + uid + ']');
            refreshAccountsHealth();
            loadOcCatalog();
        } else {
            uiLog('刷新失败 [' + uid + ']: ' + (d.error || ''));
        }
    })
    .catch(e => uiLog('刷新异常: ' + String(e)));
}

function deleteUser(uid) {
    if (!confirm('确定删除账号 ' + uid + '?')) return;
    fetch('/api/delete_user', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: uid })
    })
    .then(r => r.json())
    .then(function(d) {
        if (d.success) {
            uiLog('已删除 ' + uid);
            refreshAccountsHealth();
            loadOcCatalog();
        } else {
            userAlert(d.error || '失败');
        }
    })
    .catch(e => userAlert(String(e)));
}

function importCredentials() {
    var c = document.getElementById('batchCredentials').value;
    fetch('/api/import_credentials', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ credentials: c })
    })
    .then(r => r.json())
    .then(function(d) {
        if (d.success) {
            userAlert('导入成功: ' + d.message);
            document.getElementById('batchCredentials').value = '';
            refreshAccountsHealth();
        } else {
            userAlert('导入失败: ' + d.error);
        }
    })
    .catch(e => userAlert(String(e)));
}

// ==================== OC Catalog ====================
function loadOcCatalog() {
    fetch('/api/oc_catalog')
        .then(r => r.json())
        .then(function(d) {
            var rows = d.relay_entries || [];
            var meta = document.getElementById('ocRelayMeta');
            var nu = d.relay_unique_keys;
            meta.textContent = rows.length ? (rows.length + ' 行，去重 ' + nu + ' 个密钥') : '';

            var tbody = document.getElementById('ocTableBody');
            if (!rows.length) {
                tbody.innerHTML = '<tr><td colspan="5" class="empty-state">暂无可用 OC</td></tr>';
            } else {
                tbody.innerHTML = rows.map(function(r) {
                    var def = r.is_default ? ' <span class="badge info">默认</span>' : '';
                    var ocSt = r.oc_expired === true ? '<span class="badge danger">已过期</span>' :
                               r.oc_expired === false ? '<span class="badge">有效</span>' : '--';
                    var trial = '--';
                    if (r.trial && r.trial.ok && r.trial.mmss) {
                        var cls = (r.trial.remain_sec <= 300) ? 'warning' : '';
                        trial = '<span class="badge ' + cls + '">' + esc(r.trial.mmss) + '</span>';
                    } else if (r.trial && r.trial.no_account) {
                        trial = '<span class="badge warning">无到期</span>';
                    }
                    return '<tr>' +
                        '<td>' + esc(r.title || '') + def + '</td>' +
                        '<td><code>' + esc(r.preview || '') + '</code></td>' +
                        '<td>' + esc(r.saved_at || '--') + '</td>' +
                        '<td>' + ocSt + '</td>' +
                        '<td>' + trial + '</td>' +
                    '</tr>';
                }).join('');
            }

            // History
            var hist = d.history || [];
            var histEl = document.getElementById('ocHistoryList');
            if (!hist.length) {
                histEl.innerHTML = '<div class="empty-state">暂无历史</div>';
            } else {
                histEl.innerHTML = hist.map(function(e) {
                    return '<div class="history-item">' +
                        '<span class="history-time">' + esc(e.saved_at || '') + '</span>' +
                        '<span class="history-key">' + esc(e.preview || '') + '</span>' +
                        '<span class="history-reason">' + esc(e.reason || '') + '</span>' +
                    '</div>';
                }).join('');
            }
        })
        .catch(function(e) {
            document.getElementById('ocTableBody').innerHTML =
                '<tr><td colspan="5" class="empty-state" style="color:var(--danger)">' + esc(String(e)) + '</td></tr>';
        });
}

// ==================== Token Stats ====================
function loadTokenStats() {
    fetch('/api/token_stats')
        .then(r => r.json())
        .then(function(d) {
            document.getElementById('tokenInput').textContent = formatNumber(d.total_input || 0);
            document.getElementById('tokenOutput').textContent = formatNumber(d.total_output || 0);
            document.getElementById('tokenCache').textContent = formatNumber(d.total_cache || 0);
            document.getElementById('tokenTotal').textContent = formatNumber(d.total_all || 0);
            document.getElementById('statTokens').textContent = formatNumber(d.total_all || 0);

            var tbody = document.getElementById('tokenTableBody');
            var records = d.records || [];
            if (!records.length) {
                tbody.innerHTML = '<tr><td colspan="6" class="empty-state">暂无记录</td></tr>';
            } else {
                tbody.innerHTML = records.slice(0, 50).map(function(r) {
                    return '<tr>' +
                        '<td>' + esc(r.time || '') + '</td>' +
                        '<td>' + esc(r.model || '') + '</td>' +
                        '<td>' + formatNumber(r.input) + '</td>' +
                        '<td>' + formatNumber(r.output) + '</td>' +
                        '<td>' + formatNumber(r.cache || 0) + '</td>' +
                        '<td>' + formatNumber((r.input || 0) + (r.output || 0)) + '</td>' +
                    '</tr>';
                }).join('');
            }
        })
        .catch(function() {
            document.getElementById('tokenTableBody').innerHTML =
                '<tr><td colspan="6" class="empty-state">加载失败</td></tr>';
        });
}

// ==================== Logs ====================
function loadLogs() {
    fetch('/api/logs')
        .then(r => r.text())
        .then(function(html) {
            document.getElementById('logContainer').innerHTML = html;
            var el = document.getElementById('logContainer');
            el.scrollTop = el.scrollHeight;
        })
        .catch(() => {});
}

function clearLogs() {
    fetch('/api/clear_logs', { method: 'POST' })
        .then(() => {
            document.getElementById('logContainer').innerHTML = '';
        });
}

// ==================== Settings ====================
function saveOcMaxRetry() {
    var v = parseInt(document.getElementById('ocMaxRetry').value, 10);
    if (isNaN(v) || v < 1) v = 1;
    if (v > 10) v = 10;
    document.getElementById('ocMaxRetry').value = v;
    fetch('/api/update_app_state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ oc_max_retry: v })
    })
    .then(r => r.json())
    .then(function(d) { userAlert(d.success ? '已保存' : '保存失败'); })
    .catch(e => userAlert(String(e)));
}

// ==================== Dangerous ====================
function manualRefresh() {
    uiLog('手动刷新 OC...');
    fetch('/api/manual_refresh', { method: 'POST' })
        .then(r => r.json())
        .then(function(d) {
            if (d.success) {
                userAlert(d.message || '成功');
                loadOcCatalog();
                refreshAccountsHealth();
            } else {
                userAlert('失败: ' + d.error);
            }
        })
        .catch(e => userAlert(String(e)));
}

function destroyClaw() {
    if (!confirm('确定销毁 Claw 实例？此操作不可恢复！')) return;
    fetch('/api/destroy_claw', { method: 'POST' })
        .then(r => r.json())
        .then(function(d) {
            if (d.success) {
                userAlert(d.message);
                refreshAccountsHealth();
                loadOcCatalog();
            } else {
                userAlert(d.error || '失败');
            }
        })
        .catch(e => userAlert(String(e)));
}

// ==================== Init ====================
loadOverview();
loadLogs();

// Auto refresh logs every 10s
setInterval(loadLogs, 10000);

// Load settings
fetch('/api/status')
    .then(r => r.json())
    .then(function(d) {
        if (d.oc_max_retry != null) {
            document.getElementById('ocMaxRetry').value = d.oc_max_retry;
        }
    })
    .catch(() => {});
