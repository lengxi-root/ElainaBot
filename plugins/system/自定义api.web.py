#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import json
import re
import time
import traceback
import requests
from datetime import datetime
from core.plugin.PluginManager import Plugin

class CustomAPIPlugin(Plugin):
    """自定义API插件 - 支持多种API类型和返回格式"""
    priority = 15
    _is_hot_reload = True
    
    # 数据文件路径
    DATA_DIR = os.path.join(os.path.dirname(__file__), 'data', 'custom_api')
    CONFIG_FILE = os.path.join(DATA_DIR, 'api_config.json')
    TEMP_DIR = os.path.join(DATA_DIR, 'temp')  # 临时文件目录
    
    def __init__(self):
        try:
            # 确保数据目录和临时目录存在
            os.makedirs(self.DATA_DIR, exist_ok=True)
            os.makedirs(self.TEMP_DIR, exist_ok=True)
            
            # 如果配置文件不存在，创建示例配置
            if not os.path.exists(self.CONFIG_FILE):
                print(f"[自定义API] 初始化：配置文件不存在，正在创建...")
                self._create_example_config()
            else:
                print(f"[自定义API] 初始化完成，配置文件路径: {self.CONFIG_FILE}")
        except Exception as e:
            print(f"[自定义API] 初始化失败: {str(e)}")
            import traceback
            traceback.print_exc()
    
    @classmethod
    def _create_example_config(cls):
        """创建空配置文件"""
        # 确保数据目录存在
        os.makedirs(cls.DATA_DIR, exist_ok=True)
        
        example_config = {
            "apis": []
        }
        
        try:
            with open(cls.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(example_config, f, ensure_ascii=False, indent=2)
            print(f"[自定义API] 已创建配置文件: {cls.CONFIG_FILE}")
        except Exception as e:
            print(f"[自定义API] 创建配置文件失败: {str(e)}")
            import traceback
            traceback.print_exc()
    
    @classmethod
    def _load_config(cls):
        """加载API配置"""
        try:
            # 确保数据目录存在
            os.makedirs(cls.DATA_DIR, exist_ok=True)
            
            if os.path.exists(cls.CONFIG_FILE):
                with open(cls.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            else:
                # 配置文件不存在，创建默认配置
                print(f"[自定义API] 配置文件不存在，正在创建: {cls.CONFIG_FILE}")
                cls._create_example_config()
                return {"apis": []}
        except Exception as e:
            print(f"[自定义API] 加载配置失败: {str(e)}")
            import traceback
            traceback.print_exc()
            # 返回默认空配置，确保不会中断程序运行
            return {"apis": []}
    
    @classmethod
    def _save_config(cls, config):
        """保存API配置"""
        try:
            # 确保数据目录存在
            os.makedirs(cls.DATA_DIR, exist_ok=True)
            
            with open(cls.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"[自定义API] 保存配置失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return False
    
    @classmethod
    def get_regex_handlers(cls):
        """动态注册处理器"""
        handlers = {}
        config = cls._load_config()
        
        # 为每个启用的API注册处理器
        for api in config.get('apis', []):
            if api.get('enabled', False):
                regex = api.get('regex', '')
                if regex:
                    handlers[regex] = {
                        'handler': 'handle_api_request',
                        'owner_only': api.get('owner_only', False),
                        'group_only': api.get('group_only', False)
                    }
        
        return handlers
    
    @staticmethod
    def handle_api_request(event):
        """处理API请求"""
        try:
            config = CustomAPIPlugin._load_config()
            content = event.content.strip()
            
            # 找到匹配的API配置并捕获正则组
            matched_api = None
            regex_groups = []
            for api in config.get('apis', []):
                if api.get('enabled', False):
                    regex = api.get('regex', '')
                    if regex:
                        match = re.match(regex, content)
                        if match:
                            matched_api = api
                            regex_groups = match.groups()  # 捕获所有组
                            break
            
            if not matched_api:
                event.reply("未找到匹配的API配置")
                return True
            
            # 调用API，传递正则捕获组
            result = CustomAPIPlugin._call_api(matched_api, event, regex_groups)
            
            if result['success']:
                # 根据回复类型发送消息
                CustomAPIPlugin._send_response(event, matched_api, result['data'], regex_groups)
            else:
                event.reply(f"API调用失败: {result['error']}")
            
            return True
            
        except Exception as e:
            event.reply(f"处理请求时出错: {str(e)}")
            traceback.print_exc()
            return True
    
    @staticmethod
    def _call_api(api_config, event, regex_groups=[]):
        """调用API"""
        try:
            url = api_config.get('url', '')
            method = api_config.get('method', 'GET').upper()
            headers = api_config.get('headers', {})
            params = api_config.get('params', {})
            body = api_config.get('body', {})
            timeout = api_config.get('timeout', 10)
            response_type = api_config.get('response_type', 'text')
            
            # 自动删除URL中的 @referer
            if '@referer' in url:
                url = url.replace('@referer', '')
            
            # 替换参数中的变量
            url = CustomAPIPlugin._replace_variables(url, event, regex_groups)
            params = {k: CustomAPIPlugin._replace_variables(str(v), event, regex_groups) for k, v in params.items()}
            body = {k: CustomAPIPlugin._replace_variables(str(v), event, regex_groups) for k, v in body.items()}
            
            # 如果没有提供请求头或请求头为空，使用默认的浏览器请求头
            if not headers or headers == {}:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive',
                    'Cache-Control': 'no-cache',
                    'Upgrade-Insecure-Requests': '1'
                }
            
            # 发送请求，允许重定向
            if method == 'GET':
                response = requests.get(url, headers=headers, params=params, timeout=timeout, allow_redirects=True)
            elif method == 'POST':
                response = requests.post(url, headers=headers, params=params, json=body, timeout=timeout, allow_redirects=True)
            elif method == 'PUT':
                response = requests.put(url, headers=headers, params=params, json=body, timeout=timeout, allow_redirects=True)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, params=params, timeout=timeout, allow_redirects=True)
            else:
                return {'success': False, 'error': f'不支持的请求方法: {method}'}
            
            # 检查响应状态（接受200-299的状态码）
            if not (200 <= response.status_code < 300):
                return {'success': False, 'error': f'HTTP {response.status_code}: {response.reason}'}
            
            # 解析响应
            if response_type == 'json':
                data = response.json()
                # 返回完整JSON数据，由_send_response根据消息模板提取
                return {'success': True, 'data': data}
            
            elif response_type == 'text':
                return {'success': True, 'data': response.text}
            
            elif response_type == 'binary':
                return {'success': True, 'data': response.content}
            
            else:
                return {'success': False, 'error': f'不支持的响应类型: {response_type}'}
            
        except requests.Timeout:
            return {'success': False, 'error': 'API请求超时'}
        except requests.RequestException as e:
            return {'success': False, 'error': f'网络错误: {str(e)}'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def _replace_variables(text, event, regex_groups=[]):
        """替换文本中的变量"""
        if not isinstance(text, str):
            return text
        
        variables = {
            '{user_id}': event.user_id if hasattr(event, 'user_id') else '',
            '{group_id}': event.group_id if hasattr(event, 'group_id') else '',
            '{message}': event.content if hasattr(event, 'content') else '',
            '{timestamp}': str(int(time.time()))
        }
        
        # 添加正则捕获组变量 {$1}, {$2}, {$3}...
        for i, group in enumerate(regex_groups, 1):
            variables[f'{{${i}}}'] = group if group else ''
        
        for key, value in variables.items():
            text = text.replace(key, str(value))
        
        return text
    
    @staticmethod
    def _extract_json_path(data, path):
        """从JSON中提取指定路径的数据"""
        try:
            parts = path.split('.')
            result = data
            
            for part in parts:
                # 支持数组索引 data[0]
                if '[' in part and ']' in part:
                    key = part[:part.index('[')]
                    index = int(part[part.index('[') + 1:part.index(']')])
                    result = result[key][index]
                else:
                    result = result[part]
            
            return result
        except Exception as e:
            return f"JSON路径提取失败: {str(e)}"
    
    @staticmethod
    def _process_message_template(template, json_data, regex_groups=[]):
        """处理消息模板，替换{路径}和{$n}为实际值"""
        import re
        
        result = template
        
        # 先替换正则捕获组变量 {$1}, {$2}...
        for i, group in enumerate(regex_groups, 1):
            result = result.replace(f'{{${i}}}', str(group) if group else '')
        
        # 再查找所有{路径}模式（排除{$n}）
        pattern = r'\{(?!\$)([^}]+)\}'
        matches = re.findall(pattern, result)
        
        for path in matches:
            try:
                value = CustomAPIPlugin._extract_json_path(json_data, path.strip())
                result = result.replace(f'{{{path}}}', str(value))
            except Exception as e:
                # 如果提取失败，保留原始占位符或替换为错误信息
                result = result.replace(f'{{{path}}}', f'[提取失败:{path}]')
        
        return result
    
    @staticmethod
    def _send_response(event, api_config, data, regex_groups=[]):
        """发送响应消息"""
        try:
            reply_type = api_config.get('reply_type', 'text')
            response_type = api_config.get('response_type', 'text')
            message_template = api_config.get('message_template', '')
            
            # 如果有消息模板，处理模板变量
            if message_template and response_type == 'json':
                data = CustomAPIPlugin._process_message_template(message_template, data, regex_groups)
            elif message_template and response_type == 'text':
                # 文本类型：先替换正则捕获组，再替换{data}，最后替换基本变量
                result = message_template
                
                # 替换正则捕获组
                for i, group in enumerate(regex_groups, 1):
                    result = result.replace(f'{{${i}}}', str(group) if group else '')
                
                # 替换{data}为API返回的文本
                result = result.replace('{data}', str(data))
                
                # 替换基本变量
                result = CustomAPIPlugin._replace_variables(result, event, [])
                
                data = result
            
            if reply_type == 'text':
                # 普通文本消息（明确不使用markdown）
                event.reply(str(data), use_markdown=False)
            
            elif reply_type == 'markdown':
                # 原生Markdown消息
                event.reply(str(data), use_markdown=True)
            
            elif reply_type == 'template_markdown':
                # 模板Markdown消息
                template = api_config.get('markdown_template', '1')
                params = CustomAPIPlugin._parse_template_params(data, api_config, regex_groups)
                keyboard_id = api_config.get('keyboard_id', None)
                event.reply_markdown(template, tuple(params), keyboard_id)
            
            elif reply_type == 'image':
                # 图片消息
                image_url = str(data)
                image_text = api_config.get('image_text', '')
                # 替换image_text中的变量（正则捕获组和基本变量）
                image_text = CustomAPIPlugin._replace_variables(image_text, event, regex_groups)
                event.reply_image(image_url, image_text)
            
            elif reply_type == 'voice':
                # 语音消息
                voice_url = str(data)
                event.reply_voice(voice_url)
            
            elif reply_type == 'video':
                # 视频消息
                video_url = str(data)
                event.reply_video(video_url)
            
            elif reply_type == 'ark':
                # ARK卡片消息
                ark_type = api_config.get('ark_type', '23')
                params = CustomAPIPlugin._parse_ark_params(data, api_config, regex_groups)
                event.reply_ark(ark_type, tuple(params))
            
            else:
                event.reply(f"不支持的回复类型: {reply_type}")
        
        except Exception as e:
            event.reply(f"发送响应失败: {str(e)}")
            traceback.print_exc()
    
    @staticmethod
    def _parse_params_from_template(template_str):
        """从字符串解析参数，支持嵌套数组
        例如: "a,b,(c,d),(e,f,g)" -> ["a", "b", ["c", "d"], ["e", "f", "g"]]
        """
        if not template_str:
            return []
        
        params = []
        current = ""
        depth = 0
        in_array = False
        array_items = []
        
        for char in template_str:
            if char == '(' and depth == 0:
                # 开始数组
                if current.strip():
                    params.append(current.strip())
                    current = ""
                in_array = True
                depth = 1
                array_items = []
            elif char == ')' and depth == 1:
                # 结束数组
                if current.strip():
                    array_items.append(current.strip())
                    current = ""
                params.append(array_items)
                in_array = False
                depth = 0
                array_items = []
            elif char == ',' and depth == 0:
                # 顶层逗号，分隔参数
                if current.strip():
                    params.append(current.strip())
                current = ""
            elif char == ',' and depth == 1:
                # 数组内逗号，分隔数组项
                if current.strip():
                    array_items.append(current.strip())
                current = ""
            else:
                current += char
        
        # 处理最后一个参数
        if current.strip():
            params.append(current.strip())
        
        return params
    
    @staticmethod
    def _parse_template_params(data, api_config, regex_groups=[]):
        """从消息模板解析模板Markdown参数"""
        # 直接从消息模板按逗号分隔
        template_str = str(data)
        params = CustomAPIPlugin._parse_params_from_template(template_str)
        return params
    
    @staticmethod
    def _parse_ark_params(data, api_config, regex_groups=[]):
        """从消息模板解析ARK参数"""
        # 直接从消息模板按特殊格式解析
        template_str = str(data)
        params = CustomAPIPlugin._parse_params_from_template(template_str)
        return params
    
    # ========== Web管理面板 ==========
    
    @classmethod
    def get_web_routes(cls):
        """注册Web路由"""
        return {
            'path': 'custom_api',
            'menu_name': '自定义API',
            'menu_icon': 'bi-link-45deg',
            'description': '自定义API管理',
            'handler': 'render_page',
            'priority': 50,
            'api_routes': [
                {
                    'path': '/api/custom_api/list',
                    'handler': 'api_list_apis',
                    'methods': ['GET'],
                    'require_auth': True,
                    'require_token': True
                },
                {
                    'path': '/api/custom_api/get',
                    'handler': 'api_get_api',
                    'methods': ['POST'],
                    'require_auth': True,
                    'require_token': True
                },
                {
                    'path': '/api/custom_api/save',
                    'handler': 'api_save_api',
                    'methods': ['POST'],
                    'require_auth': True,
                    'require_token': True
                },
                {
                    'path': '/api/custom_api/delete',
                    'handler': 'api_delete_api',
                    'methods': ['POST'],
                    'require_auth': True,
                    'require_token': True
                },
                {
                    'path': '/api/custom_api/toggle',
                    'handler': 'api_toggle_api',
                    'methods': ['POST'],
                    'require_auth': True,
                    'require_token': True
                },
                {
                    'path': '/api/custom_api/test',
                    'handler': 'api_test_api',
                    'methods': ['POST'],
                    'require_auth': True,
                    'require_token': True
                },
                {
                    'path': '/api/custom_api/temp',
                    'handler': 'api_get_temp_file',
                    'methods': ['GET'],
                    'require_auth': False,
                    'require_token': True
                }
            ]
        }
    
    @classmethod
    def render_page(cls):
        """渲染Web页面"""
        html = """
<div class="container-fluid mt-4">
    <div class="row">
        <div class="col-12">
            <div class="card">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <h5 class="mb-0"><i class="bi bi-link-45deg me-2"></i>自定义API管理</h5>
                    <button class="btn btn-primary btn-sm" onclick="showAddApiModal()">
                        <i class="bi bi-plus-circle"></i> 添加API
                    </button>
                </div>
                <div class="card-body">
                    <div id="api-list" class="table-responsive">
                        <div class="text-center p-3">
                            <div class="spinner-border text-primary" role="status">
                                <span class="visually-hidden">加载中...</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>

<!-- 添加/编辑API模态框 -->
<div class="modal fade" id="apiModal" tabindex="-1">
    <div class="modal-dialog modal-lg">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title" id="apiModalLabel">添加API</h5>
                <div>
                    <button type="button" class="btn btn-secondary btn-sm me-2" data-bs-dismiss="modal">取消</button>
                    <button type="button" class="btn btn-info btn-sm me-2" onclick="showVariableList()">
                        <i class="bi bi-list-ul"></i> 变量列表
                    </button>
                    <button type="button" class="btn btn-success btn-sm me-2" onclick="testApi()">
                        <i class="bi bi-play-circle"></i> 测试API
                    </button>
                    <button type="button" class="btn btn-primary btn-sm me-2" onclick="saveApi()">
                        <i class="bi bi-save"></i> 保存
                    </button>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                </div>
            </div>
            <div class="modal-body">
                <form id="apiForm">
                    <input type="hidden" id="api-id">
                    
                    <!-- 基础信息 -->
                    <div class="row mb-3">
                        <div class="col-md-5">
                        <label class="form-label">API名称 *</label>
                        <input type="text" class="form-control" id="api-name" required>
                    </div>
                        <div class="col-md-5">
                        <label class="form-label">触发正则 *</label>
                            <input type="text" class="form-control" id="api-regex" placeholder="例如: 天气 (.+)" required>
                            <small class="text-muted">自动添加 ^ 和 $</small>
                    </div>
                        <div class="col-md-2">
                            <label class="form-label">启用状态</label>
                            <div class="form-check form-switch mt-2">
                                <input class="form-check-input" type="checkbox" id="api-enabled" checked>
                                <label class="form-check-label" for="api-enabled">启用</label>
                            </div>
                        </div>
                    </div>
                    
                    <div class="row mb-3">
                        <div class="col-md-10">
                        <label class="form-label">API地址 *</label>
                        <input type="url" class="form-control" id="api-url" placeholder="https://api.example.com/data" required>
                            <small class="text-muted">支持变量: {$1} {$2} - 正则捕获组 | {user_id} {group_id} {message} {timestamp}</small>
                        </div>
                        <div class="col-md-2">
                            <label class="form-label">响应类型</label>
                            <select class="form-select" id="api-response-type" onchange="updateResponseConfig()">
                                <option value="json">JSON</option>
                                <option value="text">纯文本</option>
                                <option value="binary">二进制</option>
                            </select>
                        </div>
                    </div>
                    
                    <div class="row">
                        <div class="col-md-6 mb-3">
                            <label class="form-label">请求方法</label>
                            <select class="form-select" id="api-method">
                                <option value="GET">GET</option>
                                <option value="POST">POST</option>
                                <option value="PUT">PUT</option>
                                <option value="DELETE">DELETE</option>
                            </select>
                        </div>
                        <div class="col-md-6 mb-3">
                            <label class="form-label">超时时间(秒)</label>
                            <input type="number" class="form-control" id="api-timeout" value="10" min="1" max="60">
                        </div>
                    </div>
                    
                    <!-- 高级设置（可折叠） -->
                    <div class="mb-3">
                        <a class="btn btn-sm btn-outline-secondary" data-bs-toggle="collapse" href="#advancedSettings" role="button" aria-expanded="false" aria-controls="advancedSettings">
                            <i class="bi bi-gear"></i> 高级设置
                        </a>
                        <div class="collapse mt-2" id="advancedSettings">
                            <div class="card card-body">
                    <div class="mb-3">
                        <label class="form-label">请求头 (JSON格式)</label>
                                    <textarea class="form-control" id="api-headers" rows="5" placeholder='{"User-Agent": "Mozilla/5.0 ...", "Accept": "application/json"}'></textarea>
                                    <small class="text-muted">
                                        <a href="javascript:void(0)" onclick="fillDefaultHeaders()">填充常见请求头</a>
                                    </small>
                    </div>
                    
                    <div class="mb-3">
                        <label class="form-label">URL参数 (JSON格式)</label>
                        <textarea class="form-control" id="api-params" rows="2" placeholder='{"key": "value"}'></textarea>
                    </div>
                    
                    <div class="mb-3">
                        <label class="form-label">请求体 (JSON格式，仅POST/PUT)</label>
                        <textarea class="form-control" id="api-body" rows="2" placeholder='{"data": "{message}"}'></textarea>
                    </div>
                        </div>
                        </div>
                    </div>
                    
                    <div class="mb-3" id="message-template-group">
                        <label class="form-label">消息模板</label>
                        <textarea class="form-control" id="api-message-template" rows="4" placeholder="机器人发送的内容，变量请点击变量列表查看"></textarea>
                        <small class="text-muted">点击上方"变量列表"按钮查看所有可用变量</small>
                    </div>
                    
                    <div class="row mb-3">
                        <div class="col-md-6">
                        <label class="form-label">回复类型</label>
                        <select class="form-select" id="api-reply-type" onchange="updateReplyConfig()">
                            <option value="text">普通文本</option>
                            <option value="markdown">原生Markdown</option>
                            <option value="template_markdown">模板Markdown</option>
                            <option value="image">图片</option>
                            <option value="voice">语音</option>
                            <option value="video">视频</option>
                            <option value="ark">ARK卡片</option>
                        </select>
                        </div>
                        <div class="col-md-3">
                            <label class="form-label">权限设置</label>
                            <div class="form-check mt-2">
                                <input class="form-check-input" type="checkbox" id="api-owner-only">
                                <label class="form-check-label" for="api-owner-only">仅主人</label>
                            </div>
                        </div>
                        <div class="col-md-3">
                            <label class="form-label">&nbsp;</label>
                            <div class="form-check mt-2">
                                <input class="form-check-input" type="checkbox" id="api-group-only">
                                <label class="form-check-label" for="api-group-only">仅群聊</label>
                            </div>
                        </div>
                    </div>
                    
                    <!-- 图片配置 -->
                    <div id="image-config" style="display:none;">
                        <div class="alert alert-info small" id="image-config-hint">
                            <i class="bi bi-info-circle"></i> 图片URL将从消息模板中提取。消息模板的处理结果将作为图片URL使用。
                        </div>
                        <div class="mb-3">
                            <label class="form-label">图片描述文本 (可选)</label>
                            <input type="text" class="form-control" id="api-image-text" placeholder="图片描述，支持变量 {$1} {data.xxx} {user_id} 等">
                        </div>
                    </div>
                    
                    <!-- 语音配置 -->
                    <div id="voice-config" style="display:none;">
                        <div class="alert alert-info small" id="voice-config-hint">
                            <i class="bi bi-info-circle"></i> 语音URL将从消息模板中提取。消息模板的处理结果将作为语音URL使用。
                        </div>
                    </div>
                    
                    <!-- 视频配置 -->
                    <div id="video-config" style="display:none;">
                        <div class="alert alert-info small" id="video-config-hint">
                            <i class="bi bi-info-circle"></i> 视频URL将从消息模板中提取。消息模板的处理结果将作为视频URL使用。
                        </div>
                    </div>
                    
                    <!-- 模板Markdown配置 -->
                    <div id="template-config" style="display:none;">
                        <div class="alert alert-info small">
                            <i class="bi bi-info-circle"></i> 模板参数从消息模板中提取，逗号分隔。
                            <br><strong>示例：</strong>
                            <br>• JSON响应: <code>{data.title},{data.content},{data.author}</code>
                            <br>• 纯文本响应: <code>{data},{$1},{user_id}</code>
                        </div>
                        <div class="mb-3">
                            <label class="form-label">模板ID</label>
                            <input type="text" class="form-control" id="api-markdown-template" placeholder="1">
                        </div>
                        <div class="mb-3">
                            <label class="form-label">按钮ID (可选)</label>
                            <input type="text" class="form-control" id="api-keyboard-id" placeholder="102321943_1752737844">
                        </div>
                    </div>
                    
                    <!-- ARK配置 -->
                    <div id="ark-config" style="display:none;">
                        <div class="alert alert-info small">
                            <i class="bi bi-info-circle"></i> ARK参数从消息模板中提取，逗号分隔，括号表示数组。
                            <br><strong>示例：</strong>
                            <br>• JSON响应: <code>{data.desc},{data.prompt},(项1,{data.link1}),(项2,{data.link2})</code>
                            <br>• 纯文本响应: <code>{data},{$1},(项1,链接1),(项2,链接2)</code>
                        </div>
                        <div class="mb-3">
                            <label class="form-label">ARK类型</label>
                            <select class="form-select" id="api-ark-type">
                                <option value="23">列表卡片 (23)</option>
                                <option value="24">信息卡片 (24)</option>
                                <option value="37">通知卡片 (37)</option>
                            </select>
                        </div>
                        </div>
                </form>
                    </div>
                            </div>
                        </div>
                            </div>

<!-- 测试结果模态框 -->
<div class="modal fade" id="testResultModal" tabindex="-1">
    <div class="modal-dialog modal-lg">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title">API测试结果</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
            <div class="modal-body">
                <div id="test-result-content"></div>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
            </div>
        </div>
    </div>
</div>

<!-- 变量列表模态框 -->
<div class="modal fade" id="variableListModal" tabindex="-1">
    <div class="modal-dialog modal-lg">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title"><i class="bi bi-code-square me-2"></i>可用变量列表</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
                <div class="row">
                    <div class="col-md-6">
                        <h6 class="text-primary"><i class="bi bi-braces"></i> JSON数据变量</h6>
                        <table class="table table-sm table-hover">
                            <thead>
                                <tr>
                                    <th>变量</th>
                                    <th>说明</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr>
                                    <td><code>{data.xxx}</code></td>
                                    <td>JSON路径，如 {data.title}</td>
                                </tr>
                                <tr>
                                    <td><code>{data}</code></td>
                                    <td>纯文本响应的完整内容</td>
                                </tr>
                                <tr>
                                    <td colspan="2" class="text-muted small">
                                        💡 点击"测试API"可视化选择JSON路径
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                        
                        <h6 class="text-success mt-4"><i class="bi bi-regex"></i> 正则捕获组</h6>
                        <table class="table table-sm table-hover">
                            <thead>
                                <tr>
                                    <th>变量</th>
                                    <th>说明</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr>
                                    <td><code>{$1}</code></td>
                                    <td>第1个捕获组</td>
                                </tr>
                                <tr>
                                    <td><code>{$2}</code></td>
                                    <td>第2个捕获组</td>
                                </tr>
                                <tr>
                                    <td><code>{$3}</code></td>
                                    <td>第3个捕获组...</td>
                                </tr>
                                <tr>
                                    <td colspan="2" class="text-muted small">
                                        💡 示例：正则 <code>天气 (.+)</code> 捕获城市名，用 {$1} 引用
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    
                    <div class="col-md-6">
                        <h6 class="text-warning"><i class="bi bi-person"></i> 用户/环境变量</h6>
                        <table class="table table-sm table-hover">
                            <thead>
                                <tr>
                                    <th>变量</th>
                                    <th>说明</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr>
                                    <td><code>{user_id}</code></td>
                                    <td>用户ID</td>
                                </tr>
                                <tr>
                                    <td><code>{group_id}</code></td>
                                    <td>群组ID</td>
                                </tr>
                                <tr>
                                    <td><code>{message}</code></td>
                                    <td>用户发送的原始消息</td>
                                </tr>
                                <tr>
                                    <td><code>{timestamp}</code></td>
                                    <td>Unix时间戳</td>
                                </tr>
                            </tbody>
                        </table>
                        
                        <h6 class="text-danger mt-4"><i class="bi bi-info-circle"></i> 使用说明</h6>
                        <div class="alert alert-light small mb-0">
                            <p class="mb-2"><strong>📍 适用位置：</strong></p>
                            <ul class="mb-2">
                                <li>API地址</li>
                                <li>URL参数、请求体</li>
                                <li>消息模板</li>
                                <li>图片描述</li>
                            </ul>
                            <p class="mb-2"><strong>📋 格式说明：</strong></p>
                            <ul class="mb-0">
                                <li><strong>普通文本/Markdown：</strong>换行分隔</li>
                                <li><strong>模板Markdown/ARK：</strong>逗号分隔</li>
                                <li><strong>ARK数组：</strong>用括号，如 (项1,链接1)</li>
                            </ul>
                        </div>
                    </div>
                </div>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
            </div>
        </div>
    </div>
</div>
"""
        
        script = """
let apiModal;
let testResultModal;
let variableListModal;
let currentEditingId = null;

// 延迟初始化，确保DOM已完全加载
setTimeout(function() {
    const apiModalEl = document.getElementById('apiModal');
    const testModalEl = document.getElementById('testResultModal');
    const variableModalEl = document.getElementById('variableListModal');
    if (apiModalEl) {
        apiModal = new bootstrap.Modal(apiModalEl);
    }
    if (testModalEl) {
        testResultModal = new bootstrap.Modal(testModalEl);
    }
    if (variableModalEl) {
        variableListModal = new bootstrap.Modal(variableModalEl);
    }
    loadApiList();
}, 100);

function showVariableList() {
    if (!variableListModal) {
        const variableModalEl = document.getElementById('variableListModal');
        if (variableModalEl) {
            variableListModal = new bootstrap.Modal(variableModalEl);
        }
    }
    if (variableListModal) {
        variableListModal.show();
    }
}

function loadApiList() {
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');
    
    fetch(`/web/api/plugin/custom_api/list?token=${encodeURIComponent(token)}`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                renderApiList(data.data.apis);
            } else {
                showError('加载失败: ' + data.message);
            }
        })
        .catch(error => {
            showError('网络错误: ' + error.message);
        });
}

function renderApiList(apis) {
    const listDiv = document.getElementById('api-list');
    
    if (!apis || apis.length === 0) {
        listDiv.innerHTML = '<div class="text-center text-muted p-3">暂无API配置</div>';
        return;
    }
    
    let html = '<table class="table table-hover">';
    html += '<thead><tr>';
    html += '<th style="width: 5%;">状态</th>';
    html += '<th style="width: 10%;">名称</th>';
    html += '<th style="width: 15%;">正则</th>';
    html += '<th style="width: 35%;">URL</th>';
    html += '<th style="width: 10%;">回复类型</th>';
    html += '<th style="width: 10%;">权限</th>';
    html += '<th style="width: 15%;">操作</th>';
    html += '</tr></thead><tbody>';
    
    apis.forEach(api => {
        const statusBadge = api.enabled 
            ? '<span class="badge bg-success">启用</span>' 
            : '<span class="badge bg-secondary">禁用</span>';
        
        const permissions = [];
        if (api.owner_only) permissions.push('主人');
        if (api.group_only) permissions.push('群聊');
        const permText = permissions.length > 0 ? permissions.join(', ') : '无限制';
        
        html += `<tr>
            <td>${statusBadge}</td>
            <td><strong>${api.name}</strong></td>
            <td><code style="font-size: 0.85em;">${api.regex}</code></td>
            <td><small class="text-muted">${api.url.substring(0, 60)}${api.url.length > 60 ? '...' : ''}</small></td>
            <td>${api.reply_type}</td>
            <td><small>${permText}</small></td>
            <td>
                <button class="btn btn-sm btn-outline-primary" onclick="editApi('${api.id}')" title="编辑">
                    <i class="bi bi-pencil"></i>
                </button>
                <button class="btn btn-sm btn-outline-${api.enabled ? 'warning' : 'success'}" 
                        onclick="toggleApi('${api.id}')" title="${api.enabled ? '禁用' : '启用'}">
                    <i class="bi bi-${api.enabled ? 'pause' : 'play'}-circle"></i>
                </button>
                <button class="btn btn-sm btn-outline-danger" onclick="deleteApi('${api.id}')" title="删除">
                    <i class="bi bi-trash"></i>
                </button>
            </td>
        </tr>`;
    });
    
    html += '</tbody></table>';
    listDiv.innerHTML = html;
}

function showAddApiModal() {
    currentEditingId = null;
    document.getElementById('apiModalLabel').textContent = '添加API';
    document.getElementById('apiForm').reset();
    document.getElementById('api-id').value = '';
    document.getElementById('api-enabled').checked = true;
    
    // 设置默认值
    document.getElementById('api-response-type').value = 'json';  // 默认JSON
    document.getElementById('api-reply-type').value = 'text';     // 默认普通文本
    
    // 默认填充常见请求头（适用于各种资源类型）
    const defaultHeaders = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Upgrade-Insecure-Requests": "1"
    };
    document.getElementById('api-headers').value = JSON.stringify(defaultHeaders, null, 2);
    
    updateResponseConfig();
    updateReplyConfig();
    
    // 确保高级设置是折叠的
    const advancedSettingsEl = document.getElementById('advancedSettings');
    if (advancedSettingsEl && advancedSettingsEl.classList.contains('show')) {
        const bsCollapse = bootstrap.Collapse.getInstance(advancedSettingsEl);
        if (bsCollapse) {
            bsCollapse.hide();
        }
    }
    
    // 确保Modal已初始化
    if (!apiModal) {
        const apiModalEl = document.getElementById('apiModal');
        if (apiModalEl) {
            apiModal = new bootstrap.Modal(apiModalEl);
        }
    }
    if (apiModal) {
        apiModal.show();
    }
}

function editApi(apiId) {
    currentEditingId = apiId;
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');
    
    fetch(`/web/api/plugin/custom_api/get?token=${encodeURIComponent(token)}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({api_id: apiId})
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            const api = data.data.api;
            document.getElementById('apiModalLabel').textContent = '编辑API';
            document.getElementById('api-id').value = api.id;
            document.getElementById('api-name').value = api.name;
            
            // 显示时去掉^和$
            let displayRegex = api.regex;
            if (displayRegex.startsWith('^')) {
                displayRegex = displayRegex.substring(1);
            }
            if (displayRegex.endsWith('$')) {
                displayRegex = displayRegex.substring(0, displayRegex.length - 1);
            }
            document.getElementById('api-regex').value = displayRegex;
            
            document.getElementById('api-enabled').checked = api.enabled;
            document.getElementById('api-url').value = api.url;
            document.getElementById('api-method').value = api.method;
            document.getElementById('api-timeout').value = api.timeout;
            document.getElementById('api-headers').value = JSON.stringify(api.headers || {}, null, 2);
            document.getElementById('api-params').value = JSON.stringify(api.params || {}, null, 2);
            document.getElementById('api-body').value = JSON.stringify(api.body || {}, null, 2);
            document.getElementById('api-response-type').value = api.response_type;
            document.getElementById('api-message-template').value = api.message_template || '';
            document.getElementById('api-reply-type').value = api.reply_type;
            document.getElementById('api-image-text').value = api.image_text || '';
            document.getElementById('api-markdown-template').value = api.markdown_template || '1';
            document.getElementById('api-keyboard-id').value = api.keyboard_id || '';
            document.getElementById('api-ark-type').value = api.ark_type || '23';
            document.getElementById('api-owner-only').checked = api.owner_only || false;
            document.getElementById('api-group-only').checked = api.group_only || false;
            
            // 如果有高级设置内容，自动展开
            const hasAdvancedSettings = (api.headers && Object.keys(api.headers).length > 0) ||
                                       (api.params && Object.keys(api.params).length > 0) ||
                                       (api.body && Object.keys(api.body).length > 0);
            if (hasAdvancedSettings) {
                const advancedSettingsEl = document.getElementById('advancedSettings');
                if (advancedSettingsEl) {
                    const bsCollapse = new bootstrap.Collapse(advancedSettingsEl, {show: true});
                }
            }
            
            updateResponseConfig();
            updateReplyConfig();
            
            // 确保Modal已初始化
            if (!apiModal) {
                const apiModalEl = document.getElementById('apiModal');
                if (apiModalEl) {
                    apiModal = new bootstrap.Modal(apiModalEl);
                }
            }
            if (apiModal) {
                apiModal.show();
            }
        } else {
            showError('获取API信息失败: ' + data.message);
        }
    })
    .catch(error => {
        showError('网络错误: ' + error.message);
    });
}

function saveApi() {
    const apiId = document.getElementById('api-id').value || generateId();
    
    // 验证JSON格式
    try {
        const headers = document.getElementById('api-headers').value.trim();
        const params = document.getElementById('api-params').value.trim();
        const body = document.getElementById('api-body').value.trim();
        
        if (headers && headers !== '{}') JSON.parse(headers);
        if (params && params !== '{}') JSON.parse(params);
        if (body && body !== '{}') JSON.parse(body);
    } catch (e) {
        showError('JSON格式错误: ' + e.message);
        return;
    }
    
    // 自动为正则添加^和$
    let regex = document.getElementById('api-regex').value.trim();
    if (!regex.startsWith('^')) {
        regex = '^' + regex;
    }
    if (!regex.endsWith('$')) {
        regex = regex + '$';
    }
    
    const apiData = {
        id: apiId,
        name: document.getElementById('api-name').value,
        regex: regex,
        enabled: document.getElementById('api-enabled').checked,
        url: document.getElementById('api-url').value,
        method: document.getElementById('api-method').value,
        timeout: parseInt(document.getElementById('api-timeout').value),
        headers: JSON.parse(document.getElementById('api-headers').value.trim() || '{}'),
        params: JSON.parse(document.getElementById('api-params').value.trim() || '{}'),
        body: JSON.parse(document.getElementById('api-body').value.trim() || '{}'),
        response_type: document.getElementById('api-response-type').value,
        message_template: document.getElementById('api-message-template').value,
        reply_type: document.getElementById('api-reply-type').value,
        image_text: document.getElementById('api-image-text').value,
        markdown_template: document.getElementById('api-markdown-template').value,
        keyboard_id: document.getElementById('api-keyboard-id').value,
        ark_type: document.getElementById('api-ark-type').value,
        owner_only: document.getElementById('api-owner-only').checked,
        group_only: document.getElementById('api-group-only').checked
    };
    
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');
    
    fetch(`/web/api/plugin/custom_api/save?token=${encodeURIComponent(token)}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(apiData)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showSuccess('保存成功');
            if (apiModal) {
                apiModal.hide();
            }
            loadApiList();
        } else {
            showError('保存失败: ' + data.message);
        }
    })
    .catch(error => {
        showError('网络错误: ' + error.message);
    });
}

function deleteApi(apiId) {
    if (!confirm('确定要删除这个API吗？')) return;
    
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');
    
    fetch(`/web/api/plugin/custom_api/delete?token=${encodeURIComponent(token)}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({api_id: apiId})
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showSuccess('删除成功');
            loadApiList();
        } else {
            showError('删除失败: ' + data.message);
        }
    })
    .catch(error => {
        showError('网络错误: ' + error.message);
    });
}

function toggleApi(apiId) {
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');
    
    fetch(`/web/api/plugin/custom_api/toggle?token=${encodeURIComponent(token)}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({api_id: apiId})
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showSuccess('操作成功');
            loadApiList();
        } else {
            showError('操作失败: ' + data.message);
        }
    })
    .catch(error => {
        showError('网络错误: ' + error.message);
    });
}

function testApi() {
    // 清空已选择的路径
    selectedJsonPaths = [];
    
    const apiData = {
        url: document.getElementById('api-url').value,
        method: document.getElementById('api-method').value,
        timeout: parseInt(document.getElementById('api-timeout').value),
        headers: JSON.parse(document.getElementById('api-headers').value.trim() || '{}'),
        params: JSON.parse(document.getElementById('api-params').value.trim() || '{}'),
        body: JSON.parse(document.getElementById('api-body').value.trim() || '{}'),
        response_type: document.getElementById('api-response-type').value,
        message_template: document.getElementById('api-message-template').value
    };
    
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');
    
    document.getElementById('test-result-content').innerHTML = '<div class="text-center"><div class="spinner-border"></div><p class="mt-2">测试中...</p></div>';
    
    // 确保Modal已初始化
    if (!testResultModal) {
        const testModalEl = document.getElementById('testResultModal');
        if (testModalEl) {
            testResultModal = new bootstrap.Modal(testModalEl);
        }
    }
    if (testResultModal) {
        testResultModal.show();
    }
    
    fetch(`/web/api/plugin/custom_api/test?token=${encodeURIComponent(token)}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(apiData)
    })
    .then(response => {
        console.log('Response status:', response.status);
        console.log('Response headers:', response.headers.get('content-type'));
        
        if (!response.ok) {
            throw new Error(`HTTP错误: ${response.status} ${response.statusText}`);
        }
        
        return response.text().then(text => {
            console.log('Response text:', text);
            try {
                return JSON.parse(text);
            } catch (e) {
                console.error('JSON解析错误:', e);
                console.error('响应内容:', text);
                throw new Error(`JSON解析失败: ${text.substring(0, 100)}`);
            }
        });
    })
    .then(data => {
        console.log('Parsed data:', data);
        if (data.success) {
            const result = data.data;
            const responseType = document.getElementById('api-response-type').value;
            
            console.log('Response Type:', responseType);
            console.log('Result:', result);
            console.log('Result Type:', typeof result);
            console.log('Is Object:', typeof result === 'object');
            console.log('Is Null:', result === null);
            
            let html = '<div class="alert alert-success">测试成功</div>';
            
            // 如果是JSON类型，显示可点击的树形结构
            if (responseType === 'json' && result !== null && typeof result === 'object') {
                html += '<div class="mb-3">';
                html += '<h6>选择JSON路径 (可选择多个):</h6>';
                html += '<div class="alert alert-info small mb-2">';
                html += '<i class="bi bi-info-circle"></i> 点击下方JSON数据中的任意值，可选择多个路径';
                html += '</div>';
                html += '<div class="mb-3">';
                html += '<strong>已选择的路径：</strong>';
                html += '<div id="selected-paths-container" class="mt-2" style="min-height: 40px; padding: 8px; background: #f8f9fa; border-radius: 4px;">';
                html += '<span class="text-muted">点击下方数据值来添加路径</span>';
                html += '</div>';
                html += '<button class="btn btn-sm btn-primary mt-2 me-2" onclick="applySelectedPaths()">';
                html += '<i class="bi bi-check-circle"></i> 应用到消息模板';
                html += '</button>';
                html += '<button class="btn btn-sm btn-secondary mt-2" onclick="clearSelectedPaths()">';
                html += '<i class="bi bi-trash"></i> 清空';
                html += '</button>';
                html += '</div>';
                html += '<div class="bg-light p-3 rounded" style="max-height: 400px; overflow-y: auto;">';
                html += '<pre id="json-tree" style="margin: 0; font-family: monospace;"></pre>';
                html += '</div>';
                html += '</div>';
                
                // 渲染JSON树
                document.getElementById('test-result-content').innerHTML = html;
                const treeContainer = document.getElementById('json-tree');
                console.log('Tree Container:', treeContainer);
                if (treeContainer) {
                    renderJsonTree(result, '', treeContainer);
                    console.log('JSON Tree rendered');
                } else {
                    console.error('Tree container not found!');
                }
            } else {
                // 检查是否是二进制数据
                if (result && typeof result === 'object' && result.type === 'binary') {
                    html += '<h6>二进制数据预览:</h6>';
                    html += '<div class="alert alert-info small mb-2">';
                    html += `<i class="bi bi-info-circle"></i> 文件大小: ${result.size} 字节 | 类型: ${result.mime_type}`;
                    html += '</div>';
                    
                    // 加载base64数据
                    loadTempFileAndDisplay(result.filename, result.mime_type);
                } else {
                    // 非JSON或非对象类型，显示普通文本
            html += '<h6>响应数据:</h6>';
                    if (responseType === 'json' && typeof result !== 'object') {
                        html += '<div class="alert alert-warning small mb-2">';
                        html += '<i class="bi bi-info-circle"></i> API返回的是简单值（字符串/数字），<strong>JSON路径留空</strong>即可直接获取该值';
                        html += '</div>';
                    }
            html += '<pre class="bg-light p-3" style="max-height: 400px; overflow-y: auto;">' + 
                            JSON.stringify(result, null, 2) + '</pre>';
            document.getElementById('test-result-content').innerHTML = html;
                }
            }
        } else {
            document.getElementById('test-result-content').innerHTML = 
                '<div class="alert alert-danger">测试失败: ' + data.message + '</div>';
        }
    })
    .catch(error => {
        console.error('测试API错误:', error);
        document.getElementById('test-result-content').innerHTML = 
            '<div class="alert alert-danger"><strong>错误:</strong> ' + error.message + '</div>';
    });
}

let selectedJsonPaths = [];

function loadTempFileAndDisplay(filename, mimeType) {
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');
    
    fetch(`/web/api/plugin/custom_api/temp?filename=${filename}&token=${token}`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const base64Data = data.data.base64;
                const dataUrl = `data:${data.data.mime_type};base64,${base64Data}`;
                
                let mediaHtml = '';
                if (mimeType.startsWith('image/')) {
                    mediaHtml = `<div class="text-center p-3 border rounded">
                        <img src="${dataUrl}" style="max-width: 100%; max-height: 500px;" />
                    </div>`;
                } else if (mimeType.startsWith('video/')) {
                    mediaHtml = `<div class="text-center p-3 border rounded">
                        <video controls style="max-width: 100%; max-height: 500px;">
                            <source src="${dataUrl}" type="${mimeType}">
                        </video>
                    </div>`;
                } else if (mimeType.startsWith('audio/')) {
                    mediaHtml = `<div class="p-3 border rounded">
                        <audio controls style="width: 100%;">
                            <source src="${dataUrl}" type="${mimeType}">
                        </audio>
                    </div>`;
                } else {
                    mediaHtml = '<div class="p-3 border rounded"><p class="text-muted">无法预览此类型的文件</p></div>';
                }
                
                const existingContent = document.getElementById('test-result-content').innerHTML;
                document.getElementById('test-result-content').innerHTML = existingContent + mediaHtml;
            } else {
                console.error('加载临时文件失败:', data.message);
            }
        })
        .catch(error => {
            console.error('加载临时文件错误:', error);
        });
}

function renderJsonTree(obj, path, container, indent = 0) {
    const indentStr = '  '.repeat(indent);
    
    if (obj === null) {
        const span = document.createElement('span');
        span.textContent = 'null';
        span.style.color = '#999';
        span.style.cursor = 'pointer';
        span.style.padding = '2px 4px';
        span.style.borderRadius = '3px';
        span.onclick = (e) => {
            e.stopPropagation();
            selectJsonPath(path);
        };
        span.onmouseenter = (e) => {
            e.target.style.backgroundColor = '#e0f7fa';
            e.target.style.fontWeight = 'bold';
        };
        span.onmouseleave = (e) => {
            e.target.style.backgroundColor = '';
            e.target.style.fontWeight = 'normal';
        };
        container.appendChild(span);
    } else if (typeof obj === 'object') {
        if (Array.isArray(obj)) {
            container.appendChild(document.createTextNode('[' + String.fromCharCode(10)));
            obj.forEach((item, index) => {
                const itemPath = path ? `${path}[${index}]` : `[${index}]`;
                const line = document.createElement('span');
                line.textContent = indentStr + '  ';
                container.appendChild(line);
                renderJsonTree(item, itemPath, container, indent + 1);
                if (index < obj.length - 1) {
                    container.appendChild(document.createTextNode(','));
                }
                container.appendChild(document.createTextNode(String.fromCharCode(10)));
            });
            container.appendChild(document.createTextNode(indentStr + ']'));
        } else {
            container.appendChild(document.createTextNode('{' + String.fromCharCode(10)));
            const keys = Object.keys(obj);
            keys.forEach((key, index) => {
                const keyPath = path ? `${path}.${key}` : key;
                const line = document.createElement('span');
                line.textContent = indentStr + '  ';
                container.appendChild(line);
                
                const keySpan = document.createElement('span');
                keySpan.textContent = JSON.stringify(key);
                keySpan.style.color = '#0066cc';
                container.appendChild(keySpan);
                
                container.appendChild(document.createTextNode(': '));
                renderJsonTree(obj[key], keyPath, container, indent + 1);
                if (index < keys.length - 1) {
                    container.appendChild(document.createTextNode(','));
                }
                container.appendChild(document.createTextNode(String.fromCharCode(10)));
            });
            container.appendChild(document.createTextNode(indentStr + '}'));
        }
    } else if (typeof obj === 'string') {
        const span = document.createElement('span');
        span.textContent = JSON.stringify(obj);
        span.style.color = '#008800';
        span.style.cursor = 'pointer';
        span.style.padding = '2px 4px';
        span.style.borderRadius = '3px';
        span.onclick = (e) => {
            e.stopPropagation();
            selectJsonPath(path);
        };
        span.onmouseenter = (e) => {
            e.target.style.backgroundColor = '#e0f7fa';
            e.target.style.fontWeight = 'bold';
        };
        span.onmouseleave = (e) => {
            e.target.style.backgroundColor = '';
            e.target.style.fontWeight = 'normal';
        };
        container.appendChild(span);
    } else if (typeof obj === 'number') {
        const span = document.createElement('span');
        span.textContent = obj;
        span.style.color = '#dd0000';
        span.style.cursor = 'pointer';
        span.style.padding = '2px 4px';
        span.style.borderRadius = '3px';
        span.onclick = (e) => {
            e.stopPropagation();
            selectJsonPath(path);
        };
        span.onmouseenter = (e) => {
            e.target.style.backgroundColor = '#e0f7fa';
            e.target.style.fontWeight = 'bold';
        };
        span.onmouseleave = (e) => {
            e.target.style.backgroundColor = '';
            e.target.style.fontWeight = 'normal';
        };
        container.appendChild(span);
    } else if (typeof obj === 'boolean') {
        const span = document.createElement('span');
        span.textContent = obj;
        span.style.color = '#0000dd';
        span.style.cursor = 'pointer';
        span.style.padding = '2px 4px';
        span.style.borderRadius = '3px';
        span.onclick = (e) => {
            e.stopPropagation();
            selectJsonPath(path);
        };
        span.onmouseenter = (e) => {
            e.target.style.backgroundColor = '#e0f7fa';
            e.target.style.fontWeight = 'bold';
        };
        span.onmouseleave = (e) => {
            e.target.style.backgroundColor = '';
            e.target.style.fontWeight = 'normal';
        };
        container.appendChild(span);
    }
}

function selectJsonPath(path) {
    if (!path) return;
    
    // 避免重复添加
    if (selectedJsonPaths.includes(path)) {
        showError('路径已存在');
        return;
    }
    
    selectedJsonPaths.push(path);
    renderSelectedPaths();
}

function renderSelectedPaths() {
    const container = document.getElementById('selected-paths-container');
    if (!container) return;
    
    if (selectedJsonPaths.length === 0) {
        container.innerHTML = '<span class="text-muted">点击下方数据值来添加路径</span>';
        return;
    }
    
    let html = '';
    selectedJsonPaths.forEach((path, index) => {
        html += `<span class="badge me-2 mb-2" style="font-size: 13px; padding: 6px 10px; background: transparent; border: 1px solid #0d6efd; color: #0d6efd;">
            <code style="color: #0d6efd; background: transparent;">{${path}}</code>
            <i class="bi bi-x-circle ms-2" onclick="removeSelectedPath(${index})" style="cursor: pointer;"></i>
        </span>`;
    });
    container.innerHTML = html;
}

function removeSelectedPath(index) {
    selectedJsonPaths.splice(index, 1);
    renderSelectedPaths();
}

function clearSelectedPaths() {
    selectedJsonPaths = [];
    renderSelectedPaths();
}

function applySelectedPaths() {
    if (selectedJsonPaths.length === 0) {
        showError('请先选择至少一个路径');
        return;
    }
    
    // 构造消息模板：每行一个路径
    const template = selectedJsonPaths.map(path => `{${path}}`).join(String.fromCharCode(10));
    document.getElementById('api-message-template').value = template;
    
    // 关闭测试结果模态框
    if (testResultModal) {
        testResultModal.hide();
    }
    
    // 显示成功提示
    showSuccess(`已应用 ${selectedJsonPaths.length} 个路径到消息模板`);
    
    // 清空选择
    selectedJsonPaths = [];
}

function updateResponseConfig() {
    const responseType = document.getElementById('api-response-type').value;
    const messageTemplateGroup = document.getElementById('message-template-group');
    const replyTypeSelect = document.getElementById('api-reply-type');
    
    if (messageTemplateGroup) {
        if (responseType === 'json' || responseType === 'text') {
            messageTemplateGroup.style.display = 'block';
    } else {
            messageTemplateGroup.style.display = 'none';
        }
    }
    
    // 如果是二进制响应，限制回复类型选项
    if (replyTypeSelect) {
        const options = replyTypeSelect.options;
        for (let i = 0; i < options.length; i++) {
            const option = options[i];
            // 二进制响应只能选择图片/语音/视频
            if (responseType === 'binary') {
                option.disabled = !['image', 'voice', 'video'].includes(option.value);
            } else {
                option.disabled = false;
            }
        }
        
        // 如果当前选择的类型被禁用，自动切换
        if (replyTypeSelect.options[replyTypeSelect.selectedIndex].disabled) {
            replyTypeSelect.value = responseType === 'binary' ? 'image' : 'text';
        }
    }
    
    // 更新回复配置
    updateReplyConfig();
}

function updateReplyConfig() {
    const replyType = document.getElementById('api-reply-type').value;
    const responseType = document.getElementById('api-response-type').value;
    
    document.getElementById('image-config').style.display = 'none';
    document.getElementById('voice-config').style.display = 'none';
    document.getElementById('video-config').style.display = 'none';
    document.getElementById('template-config').style.display = 'none';
    document.getElementById('ark-config').style.display = 'none';
    
    if (replyType === 'image') {
        document.getElementById('image-config').style.display = 'block';
        // 如果响应类型是二进制，隐藏提示
        const hint = document.getElementById('image-config-hint');
        if (hint) {
            hint.style.display = responseType === 'binary' ? 'none' : 'block';
        }
    } else if (replyType === 'voice') {
        document.getElementById('voice-config').style.display = 'block';
        const hint = document.getElementById('voice-config-hint');
        if (hint) {
            hint.style.display = responseType === 'binary' ? 'none' : 'block';
        }
    } else if (replyType === 'video') {
        document.getElementById('video-config').style.display = 'block';
        const hint = document.getElementById('video-config-hint');
        if (hint) {
            hint.style.display = responseType === 'binary' ? 'none' : 'block';
        }
    } else if (replyType === 'template_markdown') {
        document.getElementById('template-config').style.display = 'block';
    } else if (replyType === 'ark') {
        document.getElementById('ark-config').style.display = 'block';
    }
}

function generateId() {
    return 'api_' + Date.now() + '_' + Math.random().toString(36).substring(2, 9);
}

function showSuccess(message) {
    // 使用Bootstrap的Toast或Alert显示成功消息
    alert(message);
}

function showError(message) {
    alert(message);
}

function fillDefaultHeaders() {
    const defaultHeaders = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Upgrade-Insecure-Requests": "1"
    };
    
    const headersTextarea = document.getElementById('api-headers');
    const currentHeaders = headersTextarea.value.trim();
    
    if (currentHeaders && currentHeaders !== '{}') {
        if (!confirm('当前已有请求头内容，是否覆盖？')) {
            return;
        }
    }
    
    headersTextarea.value = JSON.stringify(defaultHeaders, null, 2);
}
"""
        
        css = """
.table th {
    background-color: #f8f9fa;
    font-weight: 600;
}
.modal-lg {
    max-width: 900px;
}
#apiForm label {
    font-weight: 500;
}
code {
    background-color: #f8f9fa;
    padding: 2px 6px;
    border-radius: 3px;
}
#json-tree span {
    transition: all 0.2s ease;
}
#json-tree span:hover {
    transform: scale(1.05);
}
#selected-path-text {
    font-family: monospace;
    font-size: 14px;
}
#selected-paths-container .badge {
    transition: all 0.2s ease;
}
#selected-paths-container .badge:hover {
    background: #0d6efd !important;
    color: white !important;
}
#selected-paths-container .badge:hover code {
    color: white !important;
}
#variableListModal .table {
    font-size: 0.9rem;
}
#variableListModal .table code {
    background-color: #e7f3ff;
    color: #0d6efd;
    font-weight: 500;
    padding: 3px 8px;
}
#variableListModal .table tr:hover {
    background-color: #f8f9fa;
}
#variableListModal h6 {
    border-bottom: 2px solid #dee2e6;
    padding-bottom: 8px;
    margin-bottom: 15px;
}
"""
        
        return {
            'html': html,
            'script': script,
            'css': css
        }
    
    @classmethod
    def api_list_apis(cls, request_data):
        """API: 获取API列表"""
        config = cls._load_config()
        return {
            'success': True,
            'data': {
                'apis': config.get('apis', [])
            }
        }
    
    @classmethod
    def api_get_api(cls, request_data):
        """API: 获取单个API配置"""
        api_id = request_data.get('api_id')
        if not api_id:
            return {'success': False, 'message': '缺少API ID'}
        
        config = cls._load_config()
        for api in config.get('apis', []):
            if api.get('id') == api_id:
                return {
                    'success': True,
                    'data': {
                        'api': api
                    }
                }
        
        return {'success': False, 'message': 'API不存在'}
    
    @classmethod
    def api_save_api(cls, request_data):
        """API: 保存API配置"""
        try:
            config = cls._load_config()
            api_id = request_data.get('id')
            
            # 查找是否已存在
            existing_index = None
            for i, api in enumerate(config.get('apis', [])):
                if api.get('id') == api_id:
                    existing_index = i
                    break
            
            # 更新或添加
            if existing_index is not None:
                config['apis'][existing_index] = request_data
            else:
                config['apis'].append(request_data)
            
            # 保存配置
            if cls._save_config(config):
                # 热重载插件
                from core.plugin.PluginManager import PluginManager
                PluginManager.reload_plugin(cls)
                
                return {'success': True, 'message': '保存成功'}
            else:
                return {'success': False, 'message': '保存失败'}
        
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    @classmethod
    def api_delete_api(cls, request_data):
        """API: 删除API配置"""
        api_id = request_data.get('api_id')
        if not api_id:
            return {'success': False, 'message': '缺少API ID'}
        
        config = cls._load_config()
        config['apis'] = [api for api in config.get('apis', []) if api.get('id') != api_id]
        
        if cls._save_config(config):
            # 热重载插件
            from core.plugin.PluginManager import PluginManager
            PluginManager.reload_plugin(cls)
            
            return {'success': True, 'message': '删除成功'}
        else:
            return {'success': False, 'message': '删除失败'}
    
    @classmethod
    def api_toggle_api(cls, request_data):
        """API: 切换API启用状态"""
        api_id = request_data.get('api_id')
        if not api_id:
            return {'success': False, 'message': '缺少API ID'}
        
        config = cls._load_config()
        for api in config.get('apis', []):
            if api.get('id') == api_id:
                api['enabled'] = not api.get('enabled', False)
                break
        
        if cls._save_config(config):
            # 热重载插件
            from core.plugin.PluginManager import PluginManager
            PluginManager.reload_plugin(cls)
            
            return {'success': True, 'message': '操作成功'}
        else:
            return {'success': False, 'message': '操作失败'}
    
    @classmethod
    def api_get_temp_file(cls, request_data):
        """API: 获取临时文件（返回base64编码）"""
        try:
            import base64
            import mimetypes
            
            filename = request_data.get('filename')
            if not filename:
                return {'success': False, 'message': '缺少文件名'}
            
            filepath = os.path.join(cls.TEMP_DIR, filename)
            
            if not os.path.exists(filepath):
                return {'success': False, 'message': '文件不存在'}
            
            # 读取文件并转换为base64
            with open(filepath, 'rb') as f:
                file_data = f.read()
            
            # 猜测MIME类型
            mime_type, _ = mimetypes.guess_type(filepath)
            if not mime_type:
                mime_type = 'application/octet-stream'
            
            base64_data = base64.b64encode(file_data).decode('utf-8')
            
            return {
                'success': True,
                'data': {
                    'mime_type': mime_type,
                    'base64': base64_data,
                    'size': len(file_data)
                }
            }
        
        except Exception as e:
            import traceback
            print(f"获取临时文件错误: {traceback.format_exc()}")
            return {'success': False, 'message': str(e)}
    
    @classmethod
    def api_test_api(cls, request_data):
        """API: 测试API调用"""
        try:
            # 模拟event对象
            class MockEvent:
                user_id = 'test_user'
                group_id = 'test_group'
                content = 'test message'
            
            event = MockEvent()
            result = cls._call_api(request_data, event)
            
            # 统一返回格式
            if result.get('success'):
                data = result.get('data')
                
                # 处理二进制数据（保存到临时文件）
                if isinstance(data, bytes):
                    import uuid
                    import mimetypes
                    
                    # 确保临时目录存在
                    os.makedirs(cls.TEMP_DIR, exist_ok=True)
                    
                    # 生成唯一文件名
                    file_id = str(uuid.uuid4())
                    
                    # 尝试从Content-Type判断文件类型
                    response_type = request_data.get('response_type', 'binary')
                    
                    # 根据数据头部判断文件类型
                    file_ext = '.bin'
                    mime_type = 'application/octet-stream'
                    
                    if data[:2] == b'\xff\xd8':  # JPEG (检查前2字节)
                        file_ext = '.jpg'
                        mime_type = 'image/jpeg'
                    elif data[:8] == b'\x89PNG\r\n\x1a\n':  # PNG
                        file_ext = '.png'
                        mime_type = 'image/png'
                    elif data[:6] == b'GIF87a' or data[:6] == b'GIF89a':  # GIF
                        file_ext = '.gif'
                        mime_type = 'image/gif'
                    elif data[:4] == b'RIFF' and len(data) > 12 and data[8:12] == b'WEBP':  # WEBP
                        file_ext = '.webp'
                        mime_type = 'image/webp'
                    elif len(data) > 12 and data[4:8] == b'ftyp':  # MP4
                        file_ext = '.mp4'
                        mime_type = 'video/mp4'
                    elif data[:4] == b'OggS':  # OGG
                        file_ext = '.ogg'
                        mime_type = 'audio/ogg'
                    
                    filename = f'{file_id}{file_ext}'
                    filepath = os.path.join(cls.TEMP_DIR, filename)
                    
                    # 保存文件
                    with open(filepath, 'wb') as f:
                        f.write(data)
                    
                    # 返回文件信息
                    return {
                        'success': True,
                        'data': {
                            'type': 'binary',
                            'mime_type': mime_type,
                            'size': len(data),
                            'file_id': file_id,
                            'filename': filename
                        }
                    }
                
                return {'success': True, 'data': data}
            else:
                return {'success': False, 'message': result.get('error', '未知错误')}
        
        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            print(f"测试API错误: {error_detail}")
            return {'success': False, 'message': str(e)}

