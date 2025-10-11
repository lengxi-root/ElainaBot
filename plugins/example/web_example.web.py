#!/usr/bin/env python
# -*- coding: utf-8 -*-

from core.plugin.PluginManager import Plugin

class WebExamplePlugin(Plugin):
    """Web面板示例插件"""
    priority = 10
    
    @classmethod
    def get_regex_handlers(cls):
        """注册消息处理器"""
        return {
            r'^web示例$': {'handler': 'handle_command', 'owner_only': False},
        }
    
    @staticmethod
    def handle_command(event):
        """处理命令"""
        event.reply("✨ Web示例插件已加载！\n访问web管理面板，在侧边栏可以看到「Web示例」菜单。")
    
    @classmethod
    def get_web_routes(cls):
        """注册Web路由"""
        return {
            'path': 'web-example',  # URL路径
            'menu_name': 'Web示例',  # 侧边栏菜单名称
            'menu_icon': 'bi-star',  # 菜单图标（Bootstrap Icons）
            'description': '插件Web面板示例',  # 描述
            'handler': 'render_page',  # 处理函数名
            'priority': 50,  # 菜单显示优先级（数字越小越靠前）
            
            # ✨ 插件自主注册API路由示例
            'api_routes': [
                {
                    'path': '/api/web_example/get_counter',
                    'methods': ['GET'],
                    'handler': 'api_get_counter',
                    'require_auth': True,
                    'require_token': True
                },
                {
                    'path': '/api/web_example/save_counter',
                    'methods': ['POST'],
                    'handler': 'api_save_counter',
                    'require_auth': True,
                    'require_token': True
                }
            ]
        }
    
    # 计数器存储
    counter_value = 0
    
    @classmethod
    def api_get_counter(cls, request_data):
        """API: 获取计数器值"""
        return {
            'success': True,
            'data': {
                'counter': cls.counter_value,
                'timestamp': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        }
    
    @classmethod
    def api_save_counter(cls, request_data):
        """API: 保存计数器值"""
        try:
            value = request_data.get('value', 0)
            cls.counter_value = int(value)
            
            return {
                'success': True,
                'message': f'计数器已保存: {cls.counter_value}'
            }
        except Exception as e:
            return {
                'success': False,
                'message': f'保存失败: {str(e)}'
            }
    
    @staticmethod
    def render_page():
        """渲染Web页面"""
        html = '''
        <div class="row">
            <div class="col-12">
                <div class="card">
                    <div class="card-header" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white;">
                        <h5 class="mb-0"><i class="bi bi-star me-2"></i>插件Web面板示例</h5>
                    </div>
                    <div class="card-body">
                        <div class="alert alert-success">
                            <i class="bi bi-check-circle me-2"></i>
                            <strong>成功！</strong> 这是由插件自定义的web页面
                        </div>
                        
                        <h6 class="mt-4">✨ 功能演示（使用插件自主注册的API）：</h6>
                        <div class="text-center my-4 p-4 bg-light rounded">
                            <h2 id="counter" class="text-primary mb-3">0</h2>
                            <button class="btn btn-info me-2" onclick="loadCounter()">
                                <i class="bi bi-arrow-clockwise"></i> 从服务器加载
                            </button>
                            <button class="btn btn-primary me-2" onclick="incrementCounter()">
                                <i class="bi bi-plus-circle"></i> 增加
                            </button>
                            <button class="btn btn-success me-2" onclick="saveCounter()">
                                <i class="bi bi-save"></i> 保存到服务器
                            </button>
                            <button class="btn btn-secondary" onclick="resetCounter()">
                                <i class="bi bi-arrow-clockwise"></i> 重置
                            </button>
                            <div class="mt-3">
                                <small class="text-muted" id="counter-status">点击"从服务器加载"按钮获取最新计数</small>
                            </div>
                        </div>
                        
                        <hr>
                        
                        <h6>🚀 API路由演示：</h6>
                        <div class="row mb-4">
                            <div class="col-md-6">
                                <div class="card border-info">
                                    <div class="card-header bg-info text-white">
                                        <small><i class="bi bi-arrow-down-circle"></i> GET /api/plugin/web_example/get_counter</small>
                                    </div>
                                    <div class="card-body">
                                        <p class="small mb-2">获取计数器值（自动调用）</p>
                                        <code class="small">fetch('/web/api/plugin/web_example/get_counter?token=...')</code>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-6">
                                <div class="card border-success">
                                    <div class="card-header bg-success text-white">
                                        <small><i class="bi bi-arrow-up-circle"></i> POST /api/plugin/web_example/save_counter</small>
                                    </div>
                                    <div class="card-body">
                                        <p class="small mb-2">保存计数器值</p>
                                        <code class="small">fetch(..., {method: 'POST', body: JSON.stringify({value: 5})})</code>
                                    </div>
                                </div>
                            </div>
                        </div>
                        
                        <hr>
                        
                        <h6>📘 开发指南：</h6>
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <div class="card h-100 bg-light border-0">
                                    <div class="card-body">
                                        <h6 class="text-primary">1. 创建插件文件</h6>
                                        <p class="small mb-0">在插件目录下创建后缀为 <code>.web.py</code> 的文件</p>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-6 mb-3">
                                <div class="card h-100 bg-light border-0">
                                    <div class="card-body">
                                        <h6 class="text-primary">2. 实现路由方法</h6>
                                        <p class="small mb-0">在插件类中添加 <code>get_web_routes()</code> 和渲染函数</p>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-6 mb-3">
                                <div class="card h-100 bg-light border-0">
                                    <div class="card-body">
                                        <h6 class="text-primary">3. 返回页面内容</h6>
                                        <p class="small mb-0">渲染函数返回包含 <code>html</code>、<code>script</code>、<code>css</code> 的字典</p>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-6 mb-3">
                                <div class="card h-100 bg-light border-0">
                                    <div class="card-body">
                                        <h6 class="text-primary">4. 注册API路由（可选）</h6>
                                        <p class="small mb-0">在 <code>api_routes</code> 中声明API端点，无需修改app.py</p>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-6 mb-3">
                                <div class="card h-100 bg-light border-0">
                                    <div class="card-body">
                                        <h6 class="text-primary">5. 实现API处理</h6>
                                        <p class="small mb-0">添加API处理函数，返回 <code>{'success': True}</code> 格式</p>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-6 mb-3">
                                <div class="card h-100 bg-light border-0">
                                    <div class="card-body">
                                        <h6 class="text-primary">6. 自动生效</h6>
                                        <p class="small mb-0">框架自动扫描注册，热重载支持，无需重启</p>
                                    </div>
                                </div>
                            </div>
                        </div>
                        
                        <div class="mt-3">
                            <details class="mb-3">
                                <summary class="btn btn-outline-primary btn-sm">💡 查看示例代码（包含API注册）</summary>
                                <pre class="bg-dark text-light p-3 rounded mt-2" style="font-size: 13px;"><code>from core.plugin.PluginManager import Plugin

class WebExamplePlugin(Plugin):
    @classmethod
    def get_web_routes(cls):
        return {
            'path': 'my-plugin',        # URL路径
            'menu_name': '我的插件',     # 侧边栏名称
            'menu_icon': 'bi-heart',    # 图标
            'handler': 'render_page',   # 处理函数
            'priority': 50,             # 优先级
            
            # ✨ 插件自主注册API路由（无需修改app.py）
            'api_routes': [
                {
                    'path': '/api/my_plugin/data',
                    'methods': ['GET'],
                    'handler': 'api_get_data',
                    'require_auth': True
                },
                {
                    'path': '/api/my_plugin/save',
                    'methods': ['POST'],
                    'handler': 'api_save_data',
                    'require_auth': True
                }
            ]
        }
    
    @classmethod
    def api_get_data(cls, request_data):
        return {'success': True, 'data': {...}}
    
    @classmethod
    def api_save_data(cls, request_data):
        return {'success': True, 'message': '保存成功'}
    
    @staticmethod
    def render_page():
        return {
            'html': '&lt;h1&gt;Hello World&lt;/h1&gt;',
            'script': 'console.log("页面已加载");',
            'css': '.custom { color: red; }'
        }</code></pre>
                            </details>
                        </div>
                        
                        <div class="alert alert-info mb-0">
                            <i class="bi bi-info-circle me-2"></i>
                            <strong>提示：</strong> 
                            <ul class="mb-0 mt-2">
                                <li>插件支持热重载，修改后自动更新，无需重启框架</li>
                                <li>插件可以自主注册API路由，无需修改 <code>app.py</code> 文件</li>
                                <li>API会自动应用Token和Cookie验证，确保安全性</li>
                                <li>本示例展示了2个API端点的完整实现</li>
                            </ul>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        '''
        
        # 自定义JavaScript
        script = '''
        let counter = 0;
        
        // 获取token
        function getToken() {
            return new URLSearchParams(window.location.search).get('token') || '';
        }
        
        // 从服务器加载计数器值（使用插件自主注册的API）
        async function loadCounter() {
            const statusEl = document.getElementById('counter-status');
            statusEl.textContent = '正在加载...';
            
            try {
                const response = await fetch('/web/api/plugin/web_example/get_counter?token=' + getToken());
                const result = await response.json();
                
                if (result.success) {
                    counter = result.data.counter || 0;
                    updateCounterDisplay();
                    statusEl.innerHTML = '<i class="bi bi-check-circle text-success"></i> 已从服务器加载 (' + result.data.timestamp + ')';
                } else {
                    statusEl.innerHTML = '<i class="bi bi-exclamation-triangle text-danger"></i> 加载失败: ' + result.message;
                }
            } catch (error) {
                console.error('加载计数器失败:', error);
                statusEl.innerHTML = '<i class="bi bi-exclamation-triangle text-danger"></i> 请求失败: ' + error.message;
            }
        }
        
        // 保存计数器到服务器（使用插件自主注册的API）
        async function saveCounter() {
            const statusEl = document.getElementById('counter-status');
            statusEl.textContent = '正在保存...';
            
            try {
                const response = await fetch('/web/api/plugin/web_example/save_counter?token=' + getToken(), {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({value: counter})
                });
                
                const result = await response.json();
                
                if (result.success) {
                    statusEl.innerHTML = '<i class="bi bi-check-circle text-success"></i> ' + result.message;
                } else {
                    statusEl.innerHTML = '<i class="bi bi-exclamation-triangle text-danger"></i> 保存失败: ' + result.message;
                }
            } catch (error) {
                console.error('保存计数器失败:', error);
                statusEl.innerHTML = '<i class="bi bi-exclamation-triangle text-danger"></i> 请求失败: ' + error.message;
            }
        }
        
        function incrementCounter() {
            counter++;
            updateCounterDisplay();
            document.getElementById('counter-status').innerHTML = 
                '<i class="bi bi-info-circle text-info"></i> 当前值已更改，点击"保存到服务器"按钮保存';
        }
        
        function resetCounter() {
            counter = 0;
            updateCounterDisplay();
            document.getElementById('counter-status').innerHTML = 
                '<i class="bi bi-info-circle text-info"></i> 已重置，点击"保存到服务器"按钮保存';
        }
        
        function updateCounterDisplay() {
            const counterEl = document.getElementById('counter');
            counterEl.textContent = counter;
            
            // 添加动画效果
            counterEl.style.transform = 'scale(1.2)';
            setTimeout(() => {
                counterEl.style.transform = 'scale(1)';
            }, 200);
        }
        
        // 页面加载时自动从服务器加载计数器
        document.addEventListener('DOMContentLoaded', function() {
            loadCounter();
        });
        '''
        
        # 自定义CSS
        css = '''
        #counter {
            transition: transform 0.2s ease;
        }
        
        .card-body details summary {
            cursor: pointer;
            user-select: none;
        }
        
        .card-body details summary:hover {
            opacity: 0.8;
        }
        '''
        
        return {
            'html': html,
            'script': script,
            'css': css
        }

