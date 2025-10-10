let socket = null;
let systemMonitorInterval = null;
let websocketConnection = null;
let autoRefreshTimer = null;
const URL_PREFIX = "/web";
function throttle(func, delay) {
    let timeoutId;
    let lastExecTime = 0;
    return function (...args) {
        const currentTime = Date.now();
        
        if (currentTime - lastExecTime > delay) {
            func.apply(this, args);
            lastExecTime = currentTime;
        } else {
            clearTimeout(timeoutId);
            timeoutId = setTimeout(() => {
                func.apply(this, args);
                lastExecTime = Date.now();
            }, delay - (currentTime - lastExecTime));
        }
    };
}

function debounce(func, delay) {
    let timeoutId;
    return function (...args) {
        clearTimeout(timeoutId);
        timeoutId = setTimeout(() => func.apply(this, args), delay);
    };
}

function asyncLoad(func, delay = 0) {
    'requestIdleCallback' in window 
        ? requestIdleCallback(func, { timeout: delay + 100 })
        : setTimeout(func, delay);
}

function getCurrentToken() {
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get('token') || '';
}

function buildApiUrl(endpoint) {
    const token = getCurrentToken();
    if (!endpoint.startsWith('/')) endpoint = '/' + endpoint;
    if (!endpoint.startsWith('/web/')) endpoint = '/web' + endpoint;
    const separator = endpoint.includes('?') ? '&' : '?';
    return `${endpoint}${separator}token=${encodeURIComponent(token)}`;
}
function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function formatMemory(bytes) {
    if (!bytes) return '0 MB';
    const mb = bytes / (1024 * 1024);
    return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${Math.round(mb)} MB`;
}

function formatDiskSize(bytes) {
    if (!bytes) return '0 GB';
    const mb = bytes / (1024 * 1024);
    return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${Math.round(mb)} MB`;
}
function updateTextContent(id, text) {
    const element = document.getElementById(id);
    if (element) element.textContent = text;
}

function updateProgressBar(id, percent) {
    const progressBar = document.getElementById(`${id}-progress`);
    if (!progressBar) return;
    progressBar.style.width = percent + '%';
    progressBar.classList.remove('bg-warning', 'bg-danger');
    if (percent > 80) progressBar.classList.add('bg-danger');
    else if (percent > 50) progressBar.classList.add('bg-warning');
}
function updateTime() {
    const now = new Date();
    const currentTimeElement = document.getElementById('current-time');
    const currentYearElement = document.getElementById('current-year');
    if (currentTimeElement) currentTimeElement.textContent = now.toLocaleString('zh-CN');
    if (currentYearElement) currentYearElement.textContent = now.getFullYear();
}

function initDeviceSwitchLinks() {
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');
    const mobileSwitchBtn = document.getElementById('mobile-switch-btn');
    if (token && mobileSwitchBtn) mobileSwitchBtn.href = `?device=mobile&token=${token}`;
}
const pageLoadOptimizer = {
    currentPage: null,
    loadingPages: new Set(),
    
    switchPage(newPage) {
        if (this.currentPage === newPage || this.loadingPages.has(newPage)) return;
        this.loadingPages.add(newPage);
        if (this.currentPage) this.cleanupPage(this.currentPage);
        this.currentPage = newPage;
        setTimeout(() => this.loadingPages.delete(newPage), 500);
    },
    
    cleanupPage(oldPage) {
        if (window.autoRefreshTimer) {
            clearInterval(window.autoRefreshTimer);
            window.autoRefreshTimer = null;
        }
        
        switch(oldPage) {
            case 'statistics':
                if (window.statisticsChart) {
                    window.statisticsChart.destroy();
                    window.statisticsChart = null;
                }
                break;
            case 'sandbox':
                const sandboxResults = document.getElementById('sandbox-results');
                if (sandboxResults) {
                    sandboxResults.innerHTML = `
                        <div class="empty-results text-center text-muted py-4">
                            <i class="bi bi-inbox fs-1 d-block mb-2"></i>
                            <p>尚未进行测试，请配置参数后点击"开始测试"</p>
                        </div>
                    `;
                }
                break;
        }
    }
};

window.addEventListener('beforeunload', () => pageLoadOptimizer.cleanupPage(pageLoadOptimizer.currentPage));
function initSocket(pageType = 'dashboard') {
    if (socket) {
        socket.disconnect();
        socket = null;
    }
    
    if (typeof io === 'undefined') return;

    const currentUrl = new URL(window.location.href);
    const host = currentUrl.hostname;
    const port = currentUrl.port || (currentUrl.protocol === 'https:' ? '443' : '80');
    const protocol = currentUrl.protocol === 'https:' ? 'https' : 'http';
    const token = new URLSearchParams(window.location.search).get('token');
    
    socket = io(`${protocol}://${host}:${port}${URL_PREFIX}`, {
        path: "/socket.io",
        reconnectionAttempts: 10,
        reconnectionDelay: 1000,
        timeout: 20000,
        forceNew: true,
        transports: ['polling', 'websocket'],
        query: {
            token: token
        }
    });

    socket.on('connect_error', (error) => {
        setConnectionStatus(false);
        stopAutoRefresh();
        updateConnectionText(`未连接 (${error.message})`);
    });

    socket.on('connect', () => {
        console.log(`[WebSocket] ${pageType}页面连接成功`);
        setConnectionStatus(true);
        startAutoRefresh();
    });

    socket.on('disconnect', () => {
        setConnectionStatus(false);
        stopAutoRefresh();
    });

    socket.on('initial_data', (data) => {
        if (data.system_info) updateSystemInfo(data.system_info);
        if (data.logs && window.updateLogDisplay) {
            window.updateLogDisplay('received', data.logs.received_messages);
            window.updateLogDisplay('plugin', data.logs.plugin_logs);
            window.updateLogDisplay('framework', data.logs.framework_logs);
            window.updateLogDisplay('error', data.logs.error_logs);
        }
        if (data.plugins_info && window.updatePluginsInfo) {
            window.updatePluginsInfo(data.plugins_info);
        }
    });

    socket.on('new_message', (data) => window.handleNewLog?.(data));
    socket.on('system_info_update', updateSystemInfo);
    socket.on('system_info', updateSystemInfo);
    socket.on('plugins_update', (data) => window.updatePluginsInfo?.(data));
    
    socket.on('logs_update', (data) => {
        if (!window.updateLogDisplay) return;
        window.updateLogDisplay(data.type, data.logs);
        if (window.logContainers?.[data.type]) {
            window.logContainers[data.type].totalLogs = data.total;
            window.logContainers[data.type].totalPages = Math.ceil(data.total / (window.PAGE_SIZE || 20));
            window.updatePaginationInfo?.(data.type);
        }
    });
}

function startAutoRefresh() {
    if (autoRefreshTimer === null) {
        autoRefreshTimer = setInterval(() => {
            if (socket?.connected) socket.emit('get_system_info');
        }, 5000);
    }
}

function stopAutoRefresh() {
    if (autoRefreshTimer !== null) {
        clearInterval(autoRefreshTimer);
        autoRefreshTimer = null;
    }
}

function setConnectionStatus(connected) {
    const indicator = document.getElementById('connection-indicator');
    const text = document.getElementById('connection-text');
    if (!indicator || !text) return;
    indicator.className = connected ? 'connection-status connected' : 'connection-status disconnected';
    text.textContent = connected ? '已连接' : '未连接';
}

function updateConnectionText(message) {
    const text = document.getElementById('connection-text');
    if (text) text.textContent = message;
}
const updateSystemInfo = throttle(function(data) {
    if (!data) return;
    
    const cpuUsage = data.cpu_percent || 0;
    updateTextContent('cpu-text', `${cpuUsage.toFixed(1)}%`);
    const cpuProgress = document.getElementById('cpu-progress');
    if (cpuProgress) cpuProgress.style.width = `${cpuUsage}%`;
    
    if (data.cpu_cores) {
        const el = document.getElementById('cpu-cores') || document.getElementById('cores-count');
        if (el) el.textContent = data.cpu_cores;
    }
    if (data.cpu_model) updateTextContent('cpu-model', data.cpu_model);
    
    const frameworkCpu = data.framework_cpu_percent || 0;
    updateTextContent('framework-cpu', `${frameworkCpu.toFixed(1)}%`);
    
    const memPercent = data.memory_percent || 0;
    updateTextContent('memory-text', `${memPercent.toFixed(1)}%`);
    const memProgress = document.getElementById('memory-progress');
    if (memProgress) memProgress.style.width = `${memPercent}%`;
    
    updateTextContent('framework-memory-percent', `${(data.framework_memory_percent || 0).toFixed(1)}%`);
    
    let memUsedValue = data.system_memory_used || data.memory_used || 0;
    const frameworkMemoryTotal = data.framework_memory_total || 0;
    
    if (!memUsedValue) {
        if (data.memory_percent && data.system_memory_total_bytes) {
            memUsedValue = (data.system_memory_total_bytes * data.memory_percent) / (100 * 1024 * 1024);
        } else if (data.memory_percent && data.total_memory) {
            memUsedValue = data.total_memory * data.memory_percent / 100;
        } else {
            memUsedValue = frameworkMemoryTotal * 4;
        }
    }
    
    if (memUsedValue < frameworkMemoryTotal * 1.2) memUsedValue = frameworkMemoryTotal * 4;
    
    updateTextContent('total-memory', formatMemory(memUsedValue * 1024 * 1024));
    
    let memoryUsagePercent = 50;
    if (data.system_memory_total_bytes) {
        memoryUsagePercent = (memUsedValue / (data.system_memory_total_bytes / (1024 * 1024)) * 100).toFixed(1);
    } else if (data.total_memory) {
        memoryUsagePercent = (memUsedValue / data.total_memory * 100).toFixed(1);
    } else if (data.memory_percent) {
        memoryUsagePercent = data.memory_percent;
    }
    
    const totalMemoryProgress = document.getElementById('total-memory-progress');
    if (totalMemoryProgress) {
        totalMemoryProgress.style.width = `${memoryUsagePercent}%`;
        totalMemoryProgress.classList.remove('bg-success', 'bg-warning', 'bg-danger', 'bg-primary');
        if (memoryUsagePercent > 90) totalMemoryProgress.classList.add('bg-danger');
        else if (memoryUsagePercent > 70) totalMemoryProgress.classList.add('bg-warning');
        else totalMemoryProgress.classList.add('bg-success');
    }
    
    if (data.system_memory_total_bytes) {
        updateTextContent('total-system-memory', formatDiskSize(data.system_memory_total_bytes));
    } else if (data.total_memory) {
        updateTextContent('total-system-memory', `${Math.round(data.total_memory)} MB`);
    }
    
    if (data.system_memory_used || data.memory_used) {
        const usedMemValue = data.system_memory_used || data.memory_used || 0;
        updateTextContent('used-system-memory', formatMemory(usedMemValue * 1024 * 1024));
    }
    
    const frameworkMemValue = data.framework_memory_total || data.framework_memory || 0;
    updateTextContent('framework-memory-total', formatMemory(frameworkMemValue * 1024 * 1024));
    
    const gcCounts = data.gc_counts || [0, 0, 0];
    updateTextContent('gc-count', `${gcCounts[0] || 0}/${gcCounts[1] || 0}/${gcCounts[2] || 0}`);
    updateTextContent('objects-count', data.objects_count || '0');
    
    if (data.disk_info) {
        const diskInfo = data.disk_info;
        const totalMB = formatDiskSize(diskInfo.total || 0);
        const usedMB = formatDiskSize(diskInfo.used || 0);
        
        document.querySelectorAll('[id="disk-total"]').forEach(el => el.textContent = totalMB);
        document.querySelectorAll('[id="disk-used"]').forEach(el => el.textContent = usedMB);
        
        if (diskInfo.framework_usage) {
            const formatted = formatDiskSize(diskInfo.framework_usage);
            updateTextContent('framework-disk-usage', formatted);
            updateTextContent('framework-disk', formatted);
        }
        
        if (diskInfo.total && diskInfo.used) {
            const diskUsagePercent = (diskInfo.used / diskInfo.total * 100).toFixed(1);
            const diskProgress = document.getElementById('disk-progress');
            if (diskProgress) {
                diskProgress.style.width = `${diskUsagePercent}%`;
                diskProgress.classList.remove('bg-success', 'bg-warning', 'bg-danger');
                if (diskUsagePercent > 90) diskProgress.classList.add('bg-danger');
                else if (diskUsagePercent > 70) diskProgress.classList.add('bg-warning');
                else diskProgress.classList.add('bg-success');
            }
        }
    }
    
    const formatUptime = (seconds) => {
        const days = Math.floor(seconds / 86400);
        const hours = Math.floor((seconds % 86400) / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        return `${days > 0 ? days + '天 ' : ''}${hours}小时 ${minutes}分钟`;
    };
    
    if (data.uptime) updateTextContent('framework-uptime', formatUptime(data.uptime));
    if (data.system_uptime) updateTextContent('system-uptime', formatUptime(data.system_uptime));
    
    if (data.start_time) {
        const startDate = new Date(data.start_time);
        const text = !isNaN(startDate.getTime()) 
            ? startDate.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
            : data.start_time;
        updateTextContent('framework-boot-time', text);
    }
    
    if (data.boot_time) {
        const bootDate = new Date(data.boot_time);
        const text = !isNaN(bootDate.getTime())
            ? bootDate.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
            : data.boot_time;
        updateTextContent('boot-time', text);
    }
    
    if (data.system_version) {
        const el = document.getElementById('system-version') || document.getElementById('os-version');
        if (el) el.textContent = data.system_version;
    }
}, 500);
function loadRobotInfo() {
    return fetch(buildApiUrl('/api/robot_info'))
        .then(response => response.json())
        .then(data => {
            window.robotInfo = data;
            updateRobotInfo(data);
            return data;
        });
}

function updateRobotInfo(data) {
    if (!data) return;

    updateTextContent('robot-name', data.name || '未知机器人');
    updateTextContent('robot-desc', data.description || '暂无描述');

    const avatar = document.getElementById('robot-avatar');
    if (avatar && data.avatar) {
        avatar.src = data.avatar;
        avatar.style.display = 'block';
    }

    const robotLink = document.getElementById('robot-link');
    if (robotLink && data.link) {
        robotLink.href = data.link;
        robotLink.textContent = '访问机器人';
    }

    const connectionTypeEl = document.getElementById('connection-type');
    const connectionStatusEl = document.getElementById('connection-status');
    
    if (connectionTypeEl && connectionStatusEl) {
        window.websocketEnabled = data.connection_type === 'WebSocket';
        connectionTypeEl.textContent = data.connection_type || 'WebHook';
        
        if (data.connection_type === 'WebSocket') {
            connectionTypeEl.className = 'badge bg-success ms-2';
            connectionStatusEl.textContent = data.connection_status || '检测中...';
            connectionStatusEl.className = data.connection_status === '连接成功' ? 'badge bg-success' 
                : data.connection_status === '连接失败' ? 'badge bg-danger' 
                : 'badge bg-warning';
        } else {
            connectionTypeEl.className = 'badge bg-primary ms-2';
            connectionStatusEl.textContent = '接收中';
            connectionStatusEl.className = 'badge bg-success';
        }
    }
}

function refreshRobotInfo() {
    updateTextContent('robot-name', '刷新中...');
    updateTextContent('robot-desc', '正在获取最新信息...');
    loadRobotInfo();
}
function showRobotQRCode() {
    if (!window.robotInfo) {
        loadRobotInfo().then(() => showRobotQRCode());
        return;
    }
    const modal = document.createElement('div');
    modal.className = 'modal fade';
    modal.innerHTML = `
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">
                        <i class="bi bi-qr-code me-2"></i>机器人分享二维码
                    </h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body text-center">
                    <img id="qr-image" style="max-width: 100%; height: auto;" />
                    <div class="mt-3">
                        <small class="text-muted">扫描二维码添加机器人</small>
                        <br>
                        <small class="text-muted">链接: <a href="${window.robotInfo.link || '#'}" target="_blank">${window.robotInfo.link || '暂无链接'}</a></small>
                    </div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
                </div>
            </div>
        </div>
    `;
    
    document.body.appendChild(modal);
    
    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();
    
    const qrImage = modal.querySelector('#qr-image');
    const qrUrl = window.robotInfo.qr_code_api 
        ? buildApiUrl(window.robotInfo.qr_code_api)
        : window.robotInfo.link 
        ? buildApiUrl(`/api/robot_qrcode?url=${encodeURIComponent(window.robotInfo.link)}`)
        : '';
    
    if (qrUrl) qrImage.src = qrUrl;
    modal.addEventListener('hidden.bs.modal', () => document.body.removeChild(modal));
}

function initEventListeners() {
    const exportLogsBtn = document.getElementById('export-logs-btn');
    if (exportLogsBtn) {
        exportLogsBtn.addEventListener('click', () => {
            const link = document.createElement('a');
            link.href = buildApiUrl('/api/export_logs');
            link.download = 'elaina_logs.zip';
            link.click();
        });
    }
}
function setupSidebarToggle() {
    const toggleSidebar = document.getElementById('toggle-sidebar');
    const sidebar = document.getElementById('sidebar');
    const mainContent = document.getElementById('main-content');
    
    if (!toggleSidebar || !sidebar || !mainContent) return;
    
    toggleSidebar.addEventListener('click', (e) => {
        e.stopPropagation();
        
        // 移动端: 切换侧边栏显示/隐藏
        if (window.innerWidth <= 768) {
            const isVisible = sidebar.classList.contains('mobile-visible');
            
            if (isVisible) {
                sidebar.classList.remove('mobile-visible');
                document.body.classList.remove('sidebar-open');
            } else {
                sidebar.classList.add('mobile-visible');
                document.body.classList.add('sidebar-open');
            }
        } else {
            // 桌面端: 切换侧边栏展开/收起
            sidebar.classList.toggle('collapsed');
            mainContent.classList.toggle('expanded');
        }
    });
    
    // 点击遮罩层关闭侧边栏
    document.addEventListener('click', (e) => {
        if (window.innerWidth <= 768) {
            const isClickInsideSidebar = sidebar.contains(e.target);
            const isClickOnToggle = toggleSidebar.contains(e.target);
            
            if (!isClickInsideSidebar && !isClickOnToggle && sidebar.classList.contains('mobile-visible')) {
                sidebar.classList.remove('mobile-visible');
                document.body.classList.remove('sidebar-open');
            }
        }
    });
    
    // 侧边栏菜单项点击后自动关闭(移动端)
    const sidebarItems = sidebar.querySelectorAll('.sidebar-item');
    sidebarItems.forEach(item => {
        item.addEventListener('click', () => {
            if (window.innerWidth <= 768 && sidebar.classList.contains('mobile-visible')) {
                setTimeout(() => {
                    sidebar.classList.remove('mobile-visible');
                    document.body.classList.remove('sidebar-open');
                }, 200);
            }
        });
    });
}

function setupMobileNavigation() {
    const toggleSidebar = document.getElementById('toggle-sidebar');
    const sidebar = document.getElementById('sidebar');
    const mainContent = document.getElementById('main-content');
    
    if (!toggleSidebar || !sidebar || !mainContent) return;
    
    // 响应式布局调整
    const checkScreenSize = () => {
        const width = window.innerWidth;
        
        // 清理所有状态类
        sidebar.classList.remove('mobile-visible');
        document.body.classList.remove('sidebar-open');
        
        if (width <= 768) {
            // 移动端: 侧边栏默认隐藏
            sidebar.classList.remove('collapsed', 'expanded');
            mainContent.classList.remove('expanded');
        } else if (width <= 992) {
            // 平板端: 侧边栏默认收起
            sidebar.classList.add('collapsed');
            mainContent.classList.add('expanded');
        } else {
            // 桌面端: 侧边栏默认展开
            sidebar.classList.remove('collapsed');
            mainContent.classList.remove('expanded');
        }
    };
    
    checkScreenSize();
    
    // 防抖处理窗口大小变化
    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(checkScreenSize, 200);
    });
    
    // 触摸滑动手势支持(移动端)
    let touchStartX = 0;
    let touchEndX = 0;
    
    document.addEventListener('touchstart', (e) => {
        touchStartX = e.changedTouches[0].screenX;
    }, { passive: true });
    
    document.addEventListener('touchend', (e) => {
        if (window.innerWidth > 768) return;
        
        touchEndX = e.changedTouches[0].screenX;
        handleSwipeGesture();
    }, { passive: true });
    
    function handleSwipeGesture() {
        const swipeDistance = touchEndX - touchStartX;
        const minSwipeDistance = 50;
        
        // 从左边缘向右滑动 - 打开侧边栏
        if (touchStartX < 50 && swipeDistance > minSwipeDistance && !sidebar.classList.contains('mobile-visible')) {
            sidebar.classList.add('mobile-visible');
            document.body.classList.add('sidebar-open');
        }
        
        // 从任意位置向左滑动 - 关闭侧边栏
        if (swipeDistance < -minSwipeDistance && sidebar.classList.contains('mobile-visible')) {
            sidebar.classList.remove('mobile-visible');
            document.body.classList.remove('sidebar-open');
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    updateTime();
    setInterval(updateTime, 1000);
    
    initSocket();
    initEventListeners();
    initDeviceSwitchLinks();
    loadRobotInfo();
    setupSidebarToggle();
    setupMobileNavigation();
    
    setInterval(() => {
        if (socket?.connected) socket.emit('get_system_info');
    }, 3000);
});

function initOpenAPI() {}
function initStatistics() {}
