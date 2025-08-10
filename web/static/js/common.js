// =====================
// 全局变量
// =====================
let socket = null;
let systemMonitorInterval = null;
let websocketConnection = null;
let autoRefreshTimer = null;
const URL_PREFIX = "/web";

// =====================
// 工具函数
// =====================

// 性能优化：使用节流和防抖来避免阻塞主线程
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

// 异步加载函数，避免阻塞主线程
function asyncLoad(func, delay = 0) {
    if ('requestIdleCallback' in window) {
        requestIdleCallback(func, { timeout: delay + 100 });
    } else {
        setTimeout(func, delay);
    }
}

// 获取当前token参数
function getCurrentToken() {
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get('token') || '';
}

// 构建包含token的API URL
function buildApiUrl(endpoint) {
    const token = getCurrentToken();
    // 确保endpoint以/开头，并添加/web前缀
    if (!endpoint.startsWith('/')) {
        endpoint = '/' + endpoint;
    }
    if (!endpoint.startsWith('/web/')) {
        endpoint = '/web' + endpoint;
    }
    const separator = endpoint.includes('?') ? '&' : '?';
    const fullUrl = `${endpoint}${separator}token=${encodeURIComponent(token)}`;
    
    // 调试信息（开发阶段）
    if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
        console.log(`[API] 构建URL: ${endpoint} -> ${fullUrl}`);
    }
    
    return fullUrl;
}

// 格式化字节大小
function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

// 格式化内存大小（使用MB单位）
function formatMemory(bytes) {
    if (bytes === undefined || bytes === null || bytes === 0) {
        return '0 MB';
    }
    
    const mb = bytes / (1024 * 1024);
    if (mb >= 1024) {
        // 如果大于1GB，就转换为GB  
        const gb = mb / 1024;
        return gb.toFixed(2) + ' GB';
    } else {
        // 否则显示为MB
        return Math.round(mb) + ' MB';
    }
}

// 格式化硬盘大小（全部使用MB单位）
function formatDiskSize(bytes) {
    if (bytes === undefined || bytes === null || bytes === 0) {
        return '0 GB';
    }
    
    const mb = bytes / (1024 * 1024);
    if (mb >= 1024) {
        // 如果大于1GB，就转换为GB
        const gb = mb / 1024;
        return gb.toFixed(2) + ' GB';
    } else {
        // 否则显示为MB
        return Math.round(mb) + ' MB';
    }
}

// 更新文本内容
function updateTextContent(id, text) {
    const element = document.getElementById(id);
    if (element) {
        element.textContent = text;
    }
}

// 更新进度条
function updateProgressBar(id, percent) {
    const progressBar = document.getElementById(`${id}-progress`);
    if (progressBar) {
        progressBar.style.width = percent + '%';
        progressBar.classList.remove('bg-warning', 'bg-danger');
        if (percent > 80) {
            progressBar.classList.add('bg-danger');
        } else if (percent > 50) {
            progressBar.classList.add('bg-warning');
        }
    }
}

// =====================
// 时间更新功能
// =====================
function updateTime() {
    const now = new Date();
    const currentTimeElement = document.getElementById('current-time');
    if (currentTimeElement) {
        currentTimeElement.textContent = now.toLocaleString('zh-CN');
    }
    
    const currentYearElement = document.getElementById('current-year');
    if (currentYearElement) {
        currentYearElement.textContent = now.getFullYear();
    }
}

// =====================
// 初始化设备切换链接
// =====================
function initDeviceSwitchLinks() {
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');
    if (token) {
        const mobileSwitchBtn = document.getElementById('mobile-switch-btn');
        if (mobileSwitchBtn) {
            mobileSwitchBtn.href = `?device=mobile&token=${token}`;
        }
    }
}

// =====================
// 页面性能优化器
// =====================
const pageLoadOptimizer = {
    currentPage: null,
    loadingPages: new Set(),
    
    // 页面切换时的优化处理
    switchPage: function(newPage) {
        if (this.currentPage === newPage || this.loadingPages.has(newPage)) {
            return; // 避免重复加载
        }
        
        this.loadingPages.add(newPage);
        
        // 清理上一个页面的资源
        if (this.currentPage) {
            this.cleanupPage(this.currentPage);
        }
        
        this.currentPage = newPage;
        
        // 延迟清理标记，避免阻塞
        setTimeout(() => {
            this.loadingPages.delete(newPage);
        }, 500);
    },
    
    // 清理页面资源，避免内存泄漏和性能问题
    cleanupPage: function(oldPage) {
        // 停止自动刷新定时器
        if (window.autoRefreshTimer) {
            clearInterval(window.autoRefreshTimer);
            window.autoRefreshTimer = null;
        }
        
        // 清理页面特定的资源
        switch(oldPage) {
            case 'statistics':
                // 清理统计页面的Chart.js实例，释放内存
                if (window.statisticsChart) {
                    window.statisticsChart.destroy();
                    window.statisticsChart = null;
                }
                break;
                
            case 'sandbox':
                // 清理沙盒页面的测试结果，释放DOM内存
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

// 在页面切换时自动调用优化器
window.addEventListener('beforeunload', function() {
    pageLoadOptimizer.cleanupPage(pageLoadOptimizer.currentPage);
});

// =====================
// WebSocket连接功能（优化版本）
// =====================
function initSocket(pageType = 'dashboard') {
    try {
        // 先断开已有连接
        if (socket) {
            socket.disconnect();
            socket = null;
        }
        
        // 检查是否有Socket.IO库
        if (typeof io === 'undefined') {
            console.log('Socket.IO library not loaded');
            return;
        }

        // 自动适配协议和端口
        const currentUrl = new URL(window.location.href);
        const host = currentUrl.hostname;
        const port = currentUrl.port || (currentUrl.protocol === 'https:' ? '443' : '80');
        const protocol = currentUrl.protocol === 'https:' ? 'https' : 'http';
        
        // 获取token参数
        const urlParams = new URLSearchParams(window.location.search);
        const token = urlParams.get('token');
        
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



        // 初始数据处理（完全照抄老版本）
        socket.on('initial_data', function(data) {
            console.log('收到初始数据:', data);
            
            if (data.system_info) {
                updateSystemInfo(data.system_info);
            }
            
            if (data.logs) {
                if (window.updateLogDisplay && typeof window.updateLogDisplay === 'function') {
                    window.updateLogDisplay('received', data.logs.received_messages);
                    window.updateLogDisplay('plugin', data.logs.plugin_logs);
                    window.updateLogDisplay('framework', data.logs.framework_logs);
                    window.updateLogDisplay('error', data.logs.error_logs);
                }
            }
            
            if (data.plugins_info) {
                if (window.updatePluginsInfo && typeof window.updatePluginsInfo === 'function') {
                    window.updatePluginsInfo(data.plugins_info);
                }
            }
        });

        // 新消息处理（照抄老版本）
        socket.on('new_message', function(data) {
            if (window.handleNewLog && typeof window.handleNewLog === 'function') {
                window.handleNewLog(data);
            }
        });

        // 系统信息更新（照抄老版本）
        socket.on('system_info_update', function(data) {
            updateSystemInfo(data);
        });

        // 系统信息处理（照抄老版本）
        socket.on('system_info', function(data) {
            updateSystemInfo(data);
        });

        // 插件信息更新（照抄老版本）
        socket.on('plugins_update', function(data) {
            if (window.updatePluginsInfo && typeof window.updatePluginsInfo === 'function') {
                window.updatePluginsInfo(data);
            }
        });

        // 日志更新（照抄老版本）
        socket.on('logs_update', function(data) {
            if (window.updateLogDisplay && typeof window.updateLogDisplay === 'function') {
                window.updateLogDisplay(data.type, data.logs);
                if (window.logContainers && window.logContainers[data.type]) {
                    window.logContainers[data.type].totalLogs = data.total;
                    window.logContainers[data.type].totalPages = Math.ceil(data.total / (window.PAGE_SIZE || 20));
                    if (window.updatePaginationInfo && typeof window.updatePaginationInfo === 'function') {
                        window.updatePaginationInfo(data.type);
                    }
                }
            }
        });

    } catch (error) {
        console.error('Error initializing socket:', error);
    }
}

// 自动刷新功能（完全照抄老版本）
function startAutoRefresh() {
    if (autoRefreshTimer === null) {
        autoRefreshTimer = setInterval(() => {
            if (socket && socket.connected) {
                socket.emit('get_system_info');
            }
        }, 5000); // 每5秒刷新一次
    }
}

function stopAutoRefresh() {
    if (autoRefreshTimer !== null) {
        clearInterval(autoRefreshTimer);
        autoRefreshTimer = null;
    }
}

// 设置连接状态
function setConnectionStatus(connected) {
    const indicator = document.getElementById('connection-indicator');
    const text = document.getElementById('connection-text');
    
    if (indicator && text) {
        if (connected) {
            indicator.className = 'connection-status connected';
            text.textContent = '已连接';
        } else {
            indicator.className = 'connection-status disconnected';
            text.textContent = '未连接';
        }
    }
}

// 更新连接文本
function updateConnectionText(message) {
    const text = document.getElementById('connection-text');
    if (text) {
        text.textContent = message;
    }
}

// =====================
// 系统信息更新功能
// =====================
// 使用节流优化系统信息更新，避免阻塞主线程
const updateSystemInfo = throttle(function(data) {
    if (!data) return;
    
    // CPU使用率（完全照抄老版本）
    const cpuUsage = data.cpu_percent || 0;
    const cpuText = document.getElementById('cpu-text');
    const cpuProgress = document.getElementById('cpu-progress');
    if (cpuText) cpuText.textContent = cpuUsage.toFixed(1) + '%';
    if (cpuProgress) cpuProgress.style.width = cpuUsage + '%';
    
    // CPU内核数
    if (data.cpu_cores) {
        const coresElement = document.getElementById('cpu-cores') || document.getElementById('cores-count');
        if (coresElement) coresElement.textContent = data.cpu_cores;
    }
    
    // 框架CPU使用率
    const frameworkCpu = data.framework_cpu_percent || 0;
    const frameworkCpuElement = document.getElementById('framework-cpu');
    if (frameworkCpuElement) frameworkCpuElement.textContent = frameworkCpu.toFixed(1) + '%';
    
    // 内存使用率
    const memPercent = data.memory_percent || 0;
    const memText = document.getElementById('memory-text');
    const memProgress = document.getElementById('memory-progress');
    if (memText) memText.textContent = memPercent.toFixed(1) + '%';
    if (memProgress) memProgress.style.width = memPercent + '%';
    
    // 框架内存使用率
    const frameworkMemPercent = data.framework_memory_percent || 0;
    const frameworkMemPercentElement = document.getElementById('framework-memory-percent');
    if (frameworkMemPercentElement) frameworkMemPercentElement.textContent = frameworkMemPercent.toFixed(1) + '%';
    
    // 总内存使用（完全照抄老版本逻辑）
    let memUsedValue = 0;
    let totalMemValue = 0;
    let memoryUsagePercent = 0;
    
    // 先记录当前框架内存使用
    const frameworkMemoryTotal = data.framework_memory_total || 0;
    
    // 系统内存使用 - 我们希望显示的是系统所有进程使用的内存
    if (data.system_memory_used !== undefined && data.system_memory_used !== null && data.system_memory_used > 0) {
        // 这是来自系统的实际数据 - 系统所有进程使用的内存（已是MB单位）
        memUsedValue = data.system_memory_used;
    } else if (data.memory_used !== undefined && data.memory_used !== null && data.memory_used > 0) {
        // 备选：使用memory_used数据（已是MB单位）
        memUsedValue = data.memory_used;
    } else {
        // 最后尝试：使用系统总内存的使用百分比计算
        if (data.memory_percent && data.system_memory_total_bytes) {
            // 字节转换为MB
            memUsedValue = (data.system_memory_total_bytes * (data.memory_percent / 100.0)) / (1024 * 1024);
        } else if (data.memory_percent && data.total_memory) {
            // total_memory已是MB单位
            memUsedValue = data.total_memory * (data.memory_percent / 100.0);
        } else {
            // 如果没有系统数据，则使用框架内存的倍数作为估计
            memUsedValue = frameworkMemoryTotal * 4; // 框架约占系统内存的25%
        }
    }
    
    // 确保显示的内存使用量合理且不小于框架内存
    if (memUsedValue < frameworkMemoryTotal * 1.2) {
        // 如果系统内存小于框架内存的1.2倍，可能是数据异常，使用框架内存的4倍
        memUsedValue = frameworkMemoryTotal * 4;
    }
    
    const memUsedFormatted = formatMemory(memUsedValue * 1024 * 1024); // 转MB为字节后格式化
    const totalMemElement = document.getElementById('total-memory');
    if (totalMemElement) totalMemElement.textContent = memUsedFormatted;
    
    // 设置进度条
    if (data.system_memory_total_bytes && data.system_memory_total_bytes > 0) {
        totalMemValue = data.system_memory_total_bytes / (1024 * 1024); // 从字节转为MB
        memoryUsagePercent = (memUsedValue / totalMemValue * 100).toFixed(1);
    } else if (data.total_memory && data.total_memory > 0) {
        totalMemValue = data.total_memory; // 已经是MB
        memoryUsagePercent = (memUsedValue / totalMemValue * 100).toFixed(1);
    } else if (data.memory_percent) {
        memoryUsagePercent = data.memory_percent;
    } else {
        // 默认显示50%
        memoryUsagePercent = 50;
    }
    
    const totalMemoryProgress = document.getElementById('total-memory-progress');
    if (totalMemoryProgress) {
        totalMemoryProgress.style.width = memoryUsagePercent + '%';
        
        // 根据使用率改变颜色
        totalMemoryProgress.classList.remove('bg-success', 'bg-warning', 'bg-danger', 'bg-primary');
        
        if (memoryUsagePercent > 90) {
            totalMemoryProgress.classList.add('bg-danger');
        } else if (memoryUsagePercent > 70) {
            totalMemoryProgress.classList.add('bg-warning');
        } else {
            totalMemoryProgress.classList.add('bg-success');
        }
    }
    
    // 系统总内存
    if (data.system_memory_total_bytes) {
        // 使用原始字节值计算总内存
        const totalSysMemElement = document.getElementById('total-system-memory');
        if (totalSysMemElement) totalSysMemElement.textContent = formatDiskSize(data.system_memory_total_bytes);
    } else if (data.total_memory) {
        // 如果已经是MB值，正确格式化
        const totalMemory = Number(data.total_memory);
        const totalSysMemElement = document.getElementById('total-system-memory');
        if (totalSysMemElement) totalSysMemElement.textContent = Math.round(totalMemory) + ' MB';
    }
    
    // 框架内存使用
    // 确保框架内存数据有效
    const frameworkMemValue = data.framework_memory_total || data.framework_memory || 0;
    const frameworkMemTotal = formatMemory(frameworkMemValue * 1024 * 1024);  // 转换为字节再格式化
    const frameworkMemTotalElement = document.getElementById('framework-memory-total');
    if (frameworkMemTotalElement) frameworkMemTotalElement.textContent = frameworkMemTotal;
    
    // 内存管理
    const gcCounts = data.gc_counts || [0, 0, 0];
    const gcCountElement = document.getElementById('gc-count');
    if (gcCountElement) {
        gcCountElement.textContent = `${gcCounts[0] || 0}/${gcCounts[1] || 0}/${gcCounts[2] || 0}`;
    }
    const objectsCountElement = document.getElementById('objects-count');
    if (objectsCountElement) objectsCountElement.textContent = data.objects_count || '0';
    
    // 硬盘使用情况
    if (data.disk_info) {
        const diskInfo = data.disk_info;
        const totalMB = formatDiskSize(diskInfo.total || 0);
        const usedMB = formatDiskSize(diskInfo.used || 0);
        
        // 更新页面上所有相关的磁盘数据
        const diskTotalElements = document.querySelectorAll('[id="disk-total"]');
        diskTotalElements.forEach(el => { el.textContent = totalMB; });
        
        const diskUsedElements = document.querySelectorAll('[id="disk-used"]');
        diskUsedElements.forEach(el => { el.textContent = usedMB; });
        
        // 框架主目录占用空间
        if (diskInfo.framework_usage) {
            const frameworkDiskUsageElement = document.getElementById('framework-disk-usage');
            if (frameworkDiskUsageElement) frameworkDiskUsageElement.textContent = formatDiskSize(diskInfo.framework_usage);
            const frameworkDiskElement = document.getElementById('framework-disk');
            if (frameworkDiskElement) frameworkDiskElement.textContent = formatDiskSize(diskInfo.framework_usage);
        }
        
        // 更新硬盘使用进度条
        if (diskInfo.total && diskInfo.used) {
            const diskUsagePercent = (diskInfo.used / diskInfo.total * 100).toFixed(1);
            const diskProgress = document.getElementById('disk-progress');
            if (diskProgress) {
                diskProgress.style.width = diskUsagePercent + '%';
                
                // 根据使用率改变颜色
                diskProgress.classList.remove('bg-success', 'bg-warning', 'bg-danger');
                
                if (diskUsagePercent > 90) {
                    diskProgress.classList.add('bg-danger');
                } else if (diskUsagePercent > 70) {
                    diskProgress.classList.add('bg-warning');
                } else {
                    diskProgress.classList.add('bg-success');
                }
            }
        }
    }
    
    // 应用运行时间
    if (data.uptime) {
        const uptime = Number(data.uptime);
        const days = Math.floor(uptime / 86400);
        const hours = Math.floor((uptime % 86400) / 3600);
        const minutes = Math.floor((uptime % 3600) / 60);
        
        let uptimeText = '';
        if (days > 0) uptimeText += days + '天 ';
        uptimeText += hours + '小时 ' + minutes + '分钟';
        
        // 只更新顶栏的运行时间，因为卡片已删除
        const topbarUptimeElement = document.getElementById('topbar-uptime-text') || document.getElementById('uptime-text');
        if (topbarUptimeElement) topbarUptimeElement.textContent = uptimeText;
    }
    
    // 服务器运行时间
    if (data.system_uptime) {
        const sysUptime = Number(data.system_uptime);
        const days = Math.floor(sysUptime / 86400);
        const hours = Math.floor((sysUptime % 86400) / 3600);
        const minutes = Math.floor((sysUptime % 3600) / 60);
        
        let sysUptimeText = '';
        if (days > 0) sysUptimeText += days + '天 ';
        sysUptimeText += hours + '小时 ' + minutes + '分钟';
        
        const systemUptimeElement = document.getElementById('system-uptime');
        if (systemUptimeElement) systemUptimeElement.textContent = sysUptimeText;
    }
    
    // 应用启动时间
    if (data.start_time) {
        try {
            // 尝试解析日期，如果是有效格式
            const startDate = new Date(data.start_time);
            if (!isNaN(startDate.getTime())) {
                const formattedDate = startDate.toLocaleString('zh-CN', {
                    year: 'numeric', 
                    month: '2-digit', 
                    day: '2-digit',
                    hour: '2-digit', 
                    minute: '2-digit'
                });
                // 只更新顶栏的开机时间，因为卡片已删除
                const topbarStartTimeElement = document.getElementById('topbar-start-time') || document.getElementById('start-time');
                if (topbarStartTimeElement) topbarStartTimeElement.textContent = formattedDate;
            } else {
                // 如果无法解析，直接显示原始字符串
                const topbarStartTimeElement = document.getElementById('topbar-start-time') || document.getElementById('start-time');
                if (topbarStartTimeElement) topbarStartTimeElement.textContent = data.start_time;
            }
        } catch(e) {
            // 出错则显示原始字符串
            const topbarStartTimeElement = document.getElementById('topbar-start-time') || document.getElementById('start-time');
            if (topbarStartTimeElement) topbarStartTimeElement.textContent = data.start_time;
        }
    }
    
    // 服务器开机时间
    if (data.boot_time) {
        try {
            // 尝试解析日期，如果是有效格式
            const bootDate = new Date(data.boot_time);
            if (!isNaN(bootDate.getTime())) {
                const formattedDate = bootDate.toLocaleString('zh-CN', {
                    year: 'numeric', 
                    month: '2-digit', 
                    day: '2-digit',
                    hour: '2-digit', 
                    minute: '2-digit'
                });
                const bootTimeElement = document.getElementById('boot-time');
                if (bootTimeElement) bootTimeElement.textContent = formattedDate;
            } else {
                // 如果无法解析，直接显示原始字符串
                const bootTimeElement = document.getElementById('boot-time');
                if (bootTimeElement) bootTimeElement.textContent = data.boot_time;
            }
        } catch(e) {
            // 出错则显示原始字符串
            const bootTimeElement = document.getElementById('boot-time');
            if (bootTimeElement) bootTimeElement.textContent = data.boot_time;
        }
    }
    
    // 系统版本
    if (data.system_version) {
        const systemVersionElement = document.getElementById('system-version') || document.getElementById('os-version');
        if (systemVersionElement) systemVersionElement.textContent = data.system_version;
    }
}, 500); // 节流500ms，避免频繁更新阻塞主线程

// =====================
// 机器人信息功能
// =====================
function loadRobotInfo() {
    // 获取机器人信息
    const apiUrl = buildApiUrl('/api/robot_info');
    
    return fetch(apiUrl)
        .then(response => response.json())
        .then(data => {
            // 存储到全局变量
            window.robotInfo = data;
            
            updateRobotInfo(data);
            
            // 显示数据来源信息
            if (data.data_source === 'api') {
                console.log('机器人信息加载成功（来自API）:', data);
            } else if (data.data_source === 'expired_cache') {
                console.log('机器人信息加载成功（来自过期缓存）:', data);
            } else {
                console.log('机器人信息加载成功，数据来源:', data.data_source);
            }
            
            return data;
        })
        .catch(error => {
            console.error('无法获取机器人信息:', error);
            updateTextContent('robot-name', '获取信息失败');
            updateTextContent('robot-desc', '无法连接到API');
            throw error;
        });
}

function updateRobotInfo(data) {
    if (!data) return;

    // 更新机器人基本信息
    updateTextContent('robot-name', data.name || '未知机器人');
    updateTextContent('robot-desc', data.description || '暂无描述');

    // 更新头像
    const avatar = document.getElementById('robot-avatar');
    if (avatar && data.avatar) {
        avatar.src = data.avatar;
        avatar.style.display = 'block';
    }

    // 更新机器人链接
    const robotLink = document.getElementById('robot-link');
    if (robotLink && data.link) {
        robotLink.href = data.link;
        robotLink.textContent = '访问机器人';
    }

    // 更新连接状态（完全照抄老版本逻辑）
    const connectionTypeEl = document.getElementById('connection-type');
    const connectionStatusEl = document.getElementById('connection-status');
    
    if (connectionTypeEl && connectionStatusEl) {
        // 更新全局WebSocket状态
        window.websocketEnabled = data.connection_type === 'WebSocket';
        
        connectionTypeEl.textContent = data.connection_type || 'WebHook';
        if (data.connection_type === 'WebSocket') {
            connectionTypeEl.className = 'badge bg-success ms-2';
            // 显示实际的WebSocket连接状态
            connectionStatusEl.textContent = data.connection_status || '检测中...';
            if (data.connection_status === '连接成功') {
                connectionStatusEl.className = 'badge bg-success';
            } else if (data.connection_status === '连接失败') {
                connectionStatusEl.className = 'badge bg-danger';
            } else {
                connectionStatusEl.className = 'badge bg-warning';
            }
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

// 显示机器人二维码
function showRobotQRCode() {
    if (!window.robotInfo) {
        // 如果没有机器人信息，先加载
        loadRobotInfo().then(() => {
            showRobotQRCode();
        }).catch(() => {
            alert('无法获取机器人信息');
        });
        return;
    }
    
    // 动态创建模态框
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
                    <div id="qr-error" style="display: none;" class="alert alert-danger">
                        二维码生成失败，请稍后重试
                    </div>
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
    
    // 显示模态框
    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();
    
    // 加载二维码
    const qrImage = modal.querySelector('#qr-image');
    const qrError = modal.querySelector('#qr-error');
    
    qrImage.onerror = function() {
        qrImage.style.display = 'none';
        qrError.style.display = 'block';
    };
    
    // 构建二维码URL
    let qrUrl = '';
    if (window.robotInfo.qr_code_api) {
        qrUrl = buildApiUrl(window.robotInfo.qr_code_api);
    } else if (window.robotInfo.link) {
        qrUrl = buildApiUrl(`/api/robot_qrcode?url=${encodeURIComponent(window.robotInfo.link)}`);
    }
    
    if (qrUrl) {
        qrImage.src = qrUrl;
    } else {
        qrError.style.display = 'block';
        qrImage.style.display = 'none';
    }
    
    // 模态框关闭时清理DOM
    modal.addEventListener('hidden.bs.modal', function() {
        document.body.removeChild(modal);
    });
}

// =====================
// 事件监听器初始化
// =====================
function initEventListeners() {
    // 导出日志按钮
    const exportLogsBtn = document.getElementById('export-logs-btn');
    if (exportLogsBtn) {
        exportLogsBtn.addEventListener('click', function() {
            const link = document.createElement('a');
            link.href = buildApiUrl('/api/export_logs');
            link.download = 'elaina_logs.zip';
            link.click();
        });
    }
}



// =====================
// 侧边栏控制功能
// =====================
function setupSidebarToggle() {
    const toggleSidebar = document.getElementById('toggle-sidebar');
    const sidebar = document.getElementById('sidebar');
    const mainContent = document.getElementById('main-content');
    
    if (toggleSidebar && sidebar && mainContent) {
        toggleSidebar.addEventListener('click', function() {
            sidebar.classList.toggle('collapsed');
            mainContent.classList.toggle('expanded');
        });
    }
}

// 移动端适配
function setupMobileNavigation() {
    const toggleSidebar = document.getElementById('toggle-sidebar');
    const sidebar = document.getElementById('sidebar');
    const mainContent = document.getElementById('main-content');
    
    if (!toggleSidebar || !sidebar || !mainContent) return;
    
    // 检测屏幕大小变化
    function checkScreenSize() {
        if (window.innerWidth <= 576) {
            // 移动视图
            sidebar.classList.remove('collapsed');
            sidebar.classList.remove('expanded');
            mainContent.classList.remove('expanded');
        } else if (window.innerWidth <= 992) {
            // 平板视图 - 默认折叠
            sidebar.classList.add('collapsed');
            mainContent.classList.add('expanded');
        }
    }
    
    function toggleMobileSidebar() {
        sidebar.classList.toggle('mobile-visible');
    }
    
    // 初始检查
    checkScreenSize();
    
    // 监听窗口大小变化
    window.addEventListener('resize', checkScreenSize);
    
    // 点击内容区域时关闭移动端侧边栏
    mainContent.addEventListener('click', function() {
        if (window.innerWidth <= 576 && sidebar.classList.contains('mobile-visible')) {
            sidebar.classList.remove('mobile-visible');
        }
    });
}

// =====================
// 页面初始化
// =====================
document.addEventListener('DOMContentLoaded', function() {
    // 基础功能初始化
    updateTime();
    setInterval(updateTime, 1000);
    
    initSocket();
    initEventListeners();
    initDeviceSwitchLinks();
    
    // 加载机器人信息
    loadRobotInfo();
    
    // 侧边栏功能
    setupSidebarToggle();
    setupMobileNavigation();
    
    // 定期刷新系统信息 (每3秒)
    setInterval(() => {
        if (socket && socket.connected) {
            socket.emit('get_system_info');
        }
    }, 3000);
});

// 开放平台相关功能占位符
function initOpenAPI() {
    // 开放平台初始化代码将在这里实现
}

// 统计页面相关功能占位符
function initStatistics() {
    // 统计页面初始化代码将在这里实现
}