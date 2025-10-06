#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
ElainaBot 初次配置向导
检测到未配置时，自动启动配置向导（无需验证）
"""

import os
import sys
import importlib.util
import shutil
import json
import re
import ast
import logging
import platform
import subprocess
import time
import threading
import traceback
import eventlet
eventlet.monkey_patch()
from flask import Flask, request, jsonify, send_from_directory

def load_config_module():
    """加载配置模块"""
    try:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.py')
        spec = importlib.util.spec_from_file_location("config", config_path)
        config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config)
        return config
    except:
        return None

def check_initial_config():
    """检查是否为初次配置"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    marker_file = os.path.join(base_dir, 'data', '.config_completed')
    
    # 已有完成标记，无需配置
    if os.path.exists(marker_file):
        return False
    
    # 尝试加载配置
    config = load_config_module()
    if not config:
        return True
    
    # 检查必填项
    appid = str(getattr(config, 'appid', '')).strip()
    secret = str(getattr(config, 'secret', '')).strip()
    
    # 为空或为示例值则需要配置
    return not appid or not secret or appid == '102134274'

# HTML 模板（内嵌）
INITIAL_CONFIG_HTML = '''<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ElainaBot 初次配置向导</title>
    <link href="/web/static/css/vendor/bootstrap.min.css" rel="stylesheet">
    <link href="/web/static/css/vendor/bootstrap-icons.css" rel="stylesheet">
    <link rel="stylesheet" href="/web/static/css/vendor/codemirror.min.css">
    <link rel="stylesheet" href="/web/static/css/vendor/codemirror-monokai.min.css">
    <style>
        body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }
        .wizard-container { max-width: 1200px; margin: 0 auto; }
        .wizard-header { background: white; border-radius: 12px; padding: 30px; margin-bottom: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); }
        .wizard-header h1 { color: #667eea; margin: 0; }
        .wizard-header p { color: #718096; margin-top: 10px; margin-bottom: 0; }
        .config-card { background: white; border-radius: 12px; padding: 30px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); }
        #config-editor { min-height: 500px; border: 2px solid #e2e8f0; border-radius: 8px; }
        #config-editor .CodeMirror { height: auto; min-height: 500px; font-family: 'Consolas', 'Monaco', monospace; font-size: 14px; }
        #config-editor .CodeMirror-scroll { min-height: 500px; }
        .config-actions { display: flex; gap: 12px; align-items: center; }
        .btn-config-action { 
            white-space: nowrap; 
            padding: 8px 16px; 
            font-weight: 500;
            transition: all 0.2s ease;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        }
        .btn-config-action:hover { 
            transform: translateY(-2px); 
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
        }
        .btn-config-action i { margin-right: 6px; }
        .btn-finish { 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important; 
            border: none !important; 
            color: white !important; 
            font-weight: 600 !important; 
        }
        .btn-finish:hover { 
            background: linear-gradient(135deg, #7c8ef5 0%, #8a5bb5 100%) !important;
            box-shadow: 0 4px 16px rgba(102, 126, 234, 0.4) !important;
        }
        @media (max-width: 768px) {
            .d-flex.justify-content-between { flex-direction: column; align-items: flex-start !important; gap: 10px; }
            .config-actions { width: 100%; display: flex; flex-wrap: wrap; gap: 8px; }
            .btn-config-action { flex: 1 1 auto; min-width: fit-content; }
        }
        .alert-wizard { border-radius: 8px; border: none; border-left: 4px solid #667eea; }
        .config-groups-section { background: #f8f9fa; border-radius: 8px; padding: 15px; }
        .config-groups-header { font-weight: 600; color: #495057; margin-bottom: 12px; font-size: 0.95rem; display: flex; align-items: center; gap: 6px; }
        .config-groups { display: flex; gap: 10px; flex-wrap: wrap; }
        .group-btn { padding: 10px 20px; border: 2px solid #e2e8f0; border-radius: 8px; background: white; cursor: pointer; transition: all 0.2s; font-size: 0.9rem; }
        .group-btn:hover { border-color: #667eea; color: #667eea; transform: translateY(-1px); box-shadow: 0 2px 8px rgba(102, 126, 234, 0.2); }
        .group-btn.active { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border-color: #667eea; box-shadow: 0 3px 12px rgba(102, 126, 234, 0.4); }
        .config-item { border-bottom: 1px solid #f0f0f0; padding: 15px 0; }
        .config-item:last-child { border-bottom: none; }
        .config-item:has(.config-label-with-switch) { padding: 12px 0; }
        .config-label { font-weight: 600; color: #2d3748; margin-bottom: 8px; display: flex; align-items: center; gap: 10px; }
        .config-label .label-name { flex-shrink: 0; font-size: 0.95rem; color: #667eea; }
        .config-label .label-comment { font-size: 0.85rem; color: #718096; font-weight: 400; flex: 1; }
        .config-label-with-switch { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
        .config-label-text { flex: 1; display: flex; align-items: center; gap: 10px; }
        .config-label-text .label-name { flex-shrink: 0; font-size: 0.95rem; color: #667eea; font-weight: 600; }
        .config-label-text .label-comment { font-size: 0.85rem; color: #718096; font-weight: 400; flex: 1; }
        .form-control { border: 2px solid #e2e8f0; border-radius: 8px; padding: 10px; }
        .form-control:focus { border-color: #667eea; box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1); }
        .form-check { display: flex; align-items: center; gap: 10px; margin-bottom: 0; padding-left: 0; }
        .form-check-input { cursor: pointer; width: 3rem; height: 1.5rem; border: 2px solid #cbd5e0; margin: 0; flex-shrink: 0; }
        .form-check-input:checked { background-color: #667eea; border-color: #667eea; }
        .form-check-label { cursor: pointer; font-weight: 500; color: #4a5568; margin: 0; white-space: nowrap; flex-shrink: 0; }
        textarea.form-control { resize: vertical; min-height: 80px; }
        @media (max-width: 768px) {
            .config-label-with-switch { flex-direction: column; align-items: flex-start; gap: 10px; }
            .config-label-text { width: 100%; }
            .form-check { align-self: flex-end; }
        }
    </style>
</head>
<body>
    <div class="wizard-container">
        <div class="wizard-header">
            <h1><i class="bi bi-gear-fill"></i> ElainaBot 初次配置向导</h1>
            <p>欢迎使用 ElainaBot！请完成以下配置后开始使用</p>
        </div>

        <div class="config-card">
            <div class="alert alert-wizard alert-info">
                <i class="bi bi-info-circle-fill"></i> 
                <strong>提示：</strong> 请填写必填项，请提前创建好mysql数据库，频道图床和主人openid等无需填写，其他配置可使用默认值<br>
                <strong>请注意：</strong> 重启后遇到403，请访问 http://ip:你设置的端口号/web?token=你设置的access_token<br>可在Web面板安全配置查看或设置
            </div>

            <div class="d-flex justify-content-between align-items-center mb-3">
                <ul class="nav nav-tabs mb-0">
                    <li class="nav-item">
                        <a class="nav-link active" data-bs-toggle="tab" href="#simple-mode">
                            <i class="bi bi-ui-checks"></i> 简单模式
                        </a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" data-bs-toggle="tab" href="#advanced-mode">
                            <i class="bi bi-code-square"></i> 高级模式
                        </a>
                    </li>
                </ul>
                
                <div class="config-actions">
                    <button class="btn btn-secondary btn-sm btn-config-action" onclick="loadConfig()">
                        <i class="bi bi-arrow-clockwise"></i> 重新加载
                    </button>
                    <button class="btn btn-primary btn-sm btn-config-action" onclick="saveConfig()">
                        <i class="bi bi-save"></i> 保存配置
                    </button>
                    <button class="btn btn-success btn-sm btn-finish btn-config-action" onclick="finishConfig()">
                        <i class="bi bi-check-circle-fill"></i> 完成配置并启动
                    </button>
                </div>
            </div>

            <div class="tab-content">
                <!-- 简单模式 -->
                <div class="tab-pane fade show active" id="simple-mode">
                    <div class="config-groups-section mb-3">
                        <div class="config-groups-header">
                            <i class="bi bi-asterisk text-danger"></i> 必填配置
                        </div>
                        <div id="config-groups-required" class="config-groups"></div>
                    </div>
                    
                    <div class="config-groups-section">
                        <div class="config-groups-header">
                            <i class="bi bi-gear"></i> 选填配置
                        </div>
                        <div id="config-groups-optional" class="config-groups"></div>
                    </div>
                    
                    <div id="config-form"></div>
                </div>

                <!-- 高级模式 -->
                <div class="tab-pane fade" id="advanced-mode">
                    <div id="config-editor"></div>
                </div>
            </div>
        </div>
    </div>

    <script src="/web/static/js/vendor/bootstrap.bundle.min.js"></script>
    <script src="/web/static/js/vendor/codemirror.min.js"></script>
    <script src="/web/static/js/vendor/codemirror-python.min.js"></script>
    <script src="/web/static/js/vendor/codemirror-matchbrackets.min.js"></script>
    <script src="/web/static/js/vendor/codemirror-closebrackets.min.js"></script>
    <script src="/web/static/js/vendor/codemirror-active-line.min.js"></script>
    
    <script>
        let configItems = [], configGroups = {}, currentGroup = '', editor = null;

        // 必填配置组
        const REQUIRED_GROUPS = ['基础配置', 'WEBSOCKET_CONFIG', 'WEB_SECURITY', 'DB_CONFIG', 'LOG_DB_CONFIG'];
        
        // 配置组显示名称映射（可选，用于提供友好的中文名称）
        const CONFIG_DISPLAY_NAMES = {
            '基础配置': '基础配置',
            'SERVER_CONFIG': '服务器配置',
            'LOG_CONFIG': '日志配置',
            'WEBSOCKET_CONFIG': 'WebSocket配置',
            'WEB_SECURITY': 'Web面板安全配置',
            'WEB_INTERFACE': 'Web界面外观配置',
            'DB_CONFIG': '主数据库配置',
            'LOG_DB_CONFIG': '日志数据库配置',
            'COS_CONFIG': '腾讯云COS配置',
            'BILIBILI_IMAGE_BED_CONFIG': 'Bilibili图床配置'
        };
        
        // 配置组排序优先级（数字越小越靠前）
        const CONFIG_GROUP_PRIORITY = {
            '基础配置': 1,
            'WEBSOCKET_CONFIG': 2,
            'WEB_SECURITY': 3,
            'DB_CONFIG': 4,
            'LOG_DB_CONFIG': 5,
            'SERVER_CONFIG': 6,
            'LOG_CONFIG': 7,
            'WEB_INTERFACE': 8,
            'COS_CONFIG': 9,
            'BILIBILI_IMAGE_BED_CONFIG': 10
        };

        // 初始化
        document.addEventListener('DOMContentLoaded', function() {
            // 初始化编辑器
            editor = CodeMirror(document.getElementById('config-editor'), {
                mode: 'python',
                theme: 'monokai',
                lineNumbers: true,
                lineWrapping: true,
                indentUnit: 4,
                tabSize: 4,
                matchBrackets: true,
                autoCloseBrackets: true,
                styleActiveLine: true,
                viewportMargin: Infinity
            });

            // 监听标签页切换事件
            const advancedTab = document.querySelector('a[href="#advanced-mode"]');
            if (advancedTab) {
                advancedTab.addEventListener('shown.bs.tab', function() {
                    // 切换到高级模式时刷新编辑器
                    if (editor) {
                        setTimeout(function() {
                            editor.refresh();
                        }, 50);
                    }
                });
            }

            loadConfig();
        });

        async function loadConfig() {
            // 加载并解析配置
            const response = await fetch('/api/config/parse');
            const data = await response.json();

            if (!data.success) {
                alert('加载配置失败: ' + data.message);
                return;
            }

            configItems = data.items;
            configGroups = {};

            // 分组
            data.items.forEach((item, index) => {
                item.index = index;
                const group = item.is_dict_item ? item.dict_name : '基础配置';
                (configGroups[group] = configGroups[group] || []).push(item);
            });

            // 获取所有配置组并排序
            const sortGroups = groups => groups.sort((a, b) => (CONFIG_GROUP_PRIORITY[a] || 999) - (CONFIG_GROUP_PRIORITY[b] || 999));
            const allGroups = Object.keys(configGroups);
            const requiredGroups = sortGroups(allGroups.filter(key => REQUIRED_GROUPS.includes(key)));
            const optionalGroups = sortGroups(allGroups.filter(key => !REQUIRED_GROUPS.includes(key)));
            
            // 生成按钮
            const createButton = (key, isFirst) => {
                const btn = document.createElement('button');
                btn.className = 'group-btn' + (isFirst ? ' active' : '');
                btn.textContent = CONFIG_DISPLAY_NAMES[key] || key;
                btn.onclick = () => showGroup(key, btn);
                return btn;
            };
            
            const requiredDiv = document.getElementById('config-groups-required');
            const optionalDiv = document.getElementById('config-groups-optional');
            requiredDiv.innerHTML = '';
            optionalDiv.innerHTML = '';
            
            requiredGroups.forEach((key, i) => requiredDiv.appendChild(createButton(key, i === 0)));
            optionalGroups.forEach(key => optionalDiv.appendChild(createButton(key, false)));
            
            if (optionalGroups.length === 0) {
                optionalDiv.parentElement.style.display = 'none';
            }

            // 显示第一组
            if (requiredGroups[0]) showGroupInternal(requiredGroups[0]);

            // 加载高级模式
            const configResp = await fetch('/api/config/get');
            const configData = await configResp.json();
            if (configData.success && editor) {
                editor.setValue(configData.content);
                setTimeout(() => editor.refresh(), 100);
            }
        }

        function showGroup(key, btn) {
            document.querySelectorAll('.group-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            showGroupInternal(key);
        }

        function showGroupInternal(groupKey) {
            currentGroup = groupKey;
            const formDiv = document.getElementById('config-form');
            formDiv.innerHTML = '';

            (configGroups[groupKey] || []).forEach(item => {
                const itemDiv = document.createElement('div');
                itemDiv.className = 'config-item';
                const name = item.is_dict_item ? item.key_name : item.name;

                if (item.type === 'boolean') {
                    // 布尔类型：标签和开关在同一行
                    itemDiv.innerHTML = `<div class="config-label-with-switch">
                        <div class="config-label-text">
                            <span class="label-name">${name}</span>
                            ${item.comment ? `<span class="label-comment">${item.comment}</span>` : ''}
                        </div>
                        <div class="form-check form-switch">
                            <input class="form-check-input" type="checkbox" id="cfg-${item.index}" ${item.value ? 'checked' : ''}>
                            <label class="form-check-label" for="cfg-${item.index}">${item.value ? '启用' : '禁用'}</label>
                        </div>
                    </div>`;
                    const checkbox = itemDiv.querySelector('input');
                    checkbox.onchange = function() {
                        this.nextElementSibling.textContent = this.checked ? '启用' : '禁用';
                        item.value = this.checked;
                    };
                } else {
                    // 其他类型：标签在上，输入框在下
                    const labelHtml = `<span class="label-name">${name}:</span>${item.comment ? `<span class="label-comment">${item.comment}</span>` : ''}`;
                    let inputHtml = '';
                    
                    if (item.type === 'number') {
                        inputHtml = `<input type="number" class="form-control" id="cfg-${item.index}" value="${item.value}">`;
                    } else if (item.type === 'list') {
                        inputHtml = `<textarea class="form-control" id="cfg-${item.index}" rows="3">${Array.isArray(item.value) ? item.value.join('\\n') : ''}</textarea>`;
                    } else {
                        inputHtml = `<input type="text" class="form-control" id="cfg-${item.index}" value="${item.value}">`;
                    }
                    
                    itemDiv.innerHTML = `<div class="config-label">${labelHtml}</div>${inputHtml}`;
                    const input = itemDiv.querySelector('input, textarea');
                    input.onchange = () => {
                        if (item.type === 'number') item.value = parseFloat(input.value);
                        else if (item.type === 'list') item.value = input.value.split('\\n').filter(l => l.trim());
                        else item.value = input.value;
                    };
                }

                formDiv.appendChild(itemDiv);
            });
        }

        async function saveConfig() {
            const isSimpleMode = document.querySelector('.nav-link.active').getAttribute('href') === '#simple-mode';
            
            if (isSimpleMode) {
                if (!confirm('确定保存配置吗？')) return;
                const items = configItems.map(item => ({
                    name: item.name, value: item.value, type: item.type, is_dict_item: item.is_dict_item,
                    dict_name: item.dict_name, key_name: item.key_name
                }));
                const response = await fetch('/api/config/update', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({items})
                });
                const data = await response.json();
                alert(data.success ? '✅ 保存成功！' : '❌ ' + data.message);
            } else {
                if (!confirm('确定保存配置吗？\\n\\n⚠️ 将直接替换整个配置文件！')) return;
                const response = await fetch('/api/config/save', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({content: editor.getValue()})
                });
                const data = await response.json();
                alert(data.success ? '✅ 保存成功！' : '❌ ' + data.message);
            }
        }

        async function finishConfig() {
            if (!confirm('确定完成配置吗？\\n\\n请确保已填写 appid 和 secret！\\n\\n完成后将自动重启框架。')) return;

            const response = await fetch('/api/config/finish', { method: 'POST' });
            const data = await response.json();
            
            if (data.success) {
                alert('✅ 配置完成！框架正在重启，请等待几秒后刷新页面');
                setTimeout(() => window.location.reload(), 5000);
            } else {
                alert('❌ ' + data.message);
            }
        }
    </script>
</body>
</html>
'''

def start_initial_config_wizard():
    """启动初次配置向导"""
    print("\\n" + "="*60)
    print("  欢迎使用 ElainaBot！检测到首次启动，正在启动配置向导...")
    print("="*60 + "\\n")
    
    app = Flask(__name__, static_folder='web/static', static_url_path='/web/static')
    
    @app.route('/')
    @app.route('/web/')
    def index():
        return INITIAL_CONFIG_HTML
    
    @app.route('/api/config/get')
    def get_config():
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.py')
        with open(config_path, 'r', encoding='utf-8') as f:
            return jsonify({'success': True, 'content': f.read()})
    
    @app.route('/api/config/parse')
    def parse_config():
        """解析配置文件，提取配置项"""
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.py')
        
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 解析配置项
        config_items = []
        lines = content.split('\n')
        current_dict = None  # 当前正在解析的字典名称
        dict_indent = 0      # 字典的缩进级别
        
        for i, line in enumerate(lines):
                stripped = line.strip()
                
                # 跳过空行、导入语句和文档字符串
                if not stripped or stripped.startswith('"""') or stripped.startswith("'''") or stripped.startswith('import ') or stripped.startswith('from '):
                    continue
                
                # 如果是注释行，只处理作为section标题的注释（后面会有相关配置）
                if stripped.startswith('#'):
                    continue
                
                # 检测字典的开始: VAR_NAME = {
                dict_start_pattern = r'^([A-Z_][A-Z0-9_]*)\s*=\s*\{(.*)$'
                dict_match = re.match(dict_start_pattern, stripped)
                if dict_match:
                    current_dict = dict_match.group(1)
                    dict_indent = len(line) - len(line.lstrip())
                    # 如果是单行字典定义（如 VAR = {}），则不进入字典解析模式
                    if dict_match.group(2).strip() == '}':
                        current_dict = None
                    continue
                
                # 检测字典的结束: }
                if current_dict and stripped == '}':
                    current_dict = None
                    continue
                
                # 在字典内部，解析键值对
                if current_dict:
                    dict_item_match = re.match(r"^['\"]?([a-zA-Z_][a-zA-Z0-9_]*)['\"]?\s*:\s*(.+?)(?:,\s*)?(?:#\s*(.+))?$", stripped)
                    if dict_item_match:
                        key_name = dict_item_match.group(1)
                        value_str = dict_item_match.group(2).strip().rstrip(',').strip()
                        comment = dict_item_match.group(3).strip() if dict_item_match.group(3) else ''
                        
                        try:
                            value = ast.literal_eval(value_str)
                            # 只处理基本类型和字符串列表
                            if isinstance(value, bool):
                                value_type = 'boolean'
                            elif isinstance(value, (int, float)):
                                value_type = 'number'
                            elif isinstance(value, str):
                                value_type = 'string'
                            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                                value_type = 'list'
                            else:
                                continue
                            
                            config_items.append({
                                'name': f"{current_dict}.{key_name}",
                                'dict_name': current_dict,
                                'key_name': key_name,
                                'value': value,
                                'type': value_type,
                                'comment': comment,
                                'line': i,
                                'is_dict_item': True
                            })
                        except (ValueError, SyntaxError):
                            pass
                    continue
                
                # 识别简单赋值（字符串、数字、布尔值）
                match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+?)(?:\s*#\s*(.+))?$', stripped)
                if match:
                    var_name = match.group(1)
                    value_str = match.group(2).strip()
                    comment = match.group(3).strip() if match.group(3) else ''
                    
                    # 跳过字典和列表定义
                    if value_str in ['{', '['] or value_str.endswith(('{', '[')):
                        continue
                    
                    try:
                        value = ast.literal_eval(value_str)
                        # 只处理基本类型和字符串列表
                        if isinstance(value, bool):
                            value_type = 'boolean'
                        elif isinstance(value, (int, float)):
                            value_type = 'number'
                        elif isinstance(value, str):
                            value_type = 'string'
                        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                            value_type = 'list'
                        else:
                            continue
                        
                        config_items.append({
                            'name': var_name,
                            'value': value,
                            'type': value_type,
                            'comment': comment,
                            'line': i,
                            'is_dict_item': False
                        })
                    except (ValueError, SyntaxError):
                        pass
        
        return jsonify({
            'success': True,
            'items': config_items
        })
    
    @app.route('/api/config/update', methods=['POST'])
    def update_config():
        """根据表单更新配置项"""
        data = request.get_json()
        if not data or 'items' not in data:
            return jsonify({'success': False, 'message': '缺少配置项数据'}), 400
        
        items = data['items']
        
        # 读取配置文件
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.py')
        
        with open(config_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # 更新配置项
        for item in items:
            var_name = item['name']
            new_value = item['value']
            value_type = item['type']
            is_dict_item = item.get('is_dict_item', False)
            
            # 格式化新值
            if value_type == 'string':
                formatted_value = f'"{new_value}"'
            elif value_type == 'boolean':
                formatted_value = 'True' if new_value else 'False'
            elif value_type == 'number':
                formatted_value = str(new_value)
            elif value_type == 'list':
                formatted_value = '[' + ', '.join([f'"{v}"' for v in new_value]) + ']' if isinstance(new_value, list) else '[]'
            else:
                formatted_value = str(new_value)
            
            # 在文件中查找并替换，保留行尾注释
            if is_dict_item:
                # 字典项：需要在正确的字典内匹配
                dict_name = item.get('dict_name', '')
                key_name = item.get('key_name', '')
                
                # 先找到字典的定义行
                dict_start_pattern = rf'^({re.escape(dict_name)})\s*=\s*\{{'
                in_target_dict = False
                dict_depth = 0
                
                for i, line in enumerate(lines):
                    # 检测目标字典的开始
                    if re.match(dict_start_pattern, line.strip()):
                        in_target_dict = True
                        dict_depth = 1
                        continue
                    
                    # 如果在目标字典内
                    if in_target_dict:
                        # 跟踪嵌套层级
                        dict_depth += line.count('{')
                        dict_depth -= line.count('}')
                        
                        # 如果字典已经结束
                        if dict_depth == 0:
                            in_target_dict = False
                            break
                        
                        # 在字典内匹配键值对
                        match = re.match(rf"^(\s*)['\"]?({re.escape(key_name)})['\"]?\s*:\s*(.+?)(?:,\s*)?(\s*#.+)?$", line)
                        if match:
                            indent, comment = match.group(1), match.group(4) or ''
                            value_part = f"'{key_name}': {formatted_value},"
                            if comment:
                                clean_comment = comment.strip() if comment.strip().startswith('#') else '# ' + comment.strip()
                                lines[i] = f'{indent}{value_part}  {clean_comment}\n'
                            else:
                                lines[i] = f'{indent}{value_part}\n'
                            break
            else:
                # 简单变量：匹配 VAR_NAME = value
                for i, line in enumerate(lines):
                    match = re.match(rf'^(\s*)({re.escape(var_name)})\s*=\s*(.+?)(\s*#.+)?$', line)
                    if match:
                        indent, comment = match.group(1), match.group(4) or ''
                        value_part = f'{var_name} = {formatted_value}'
                        if comment:
                            clean_comment = comment.strip() if comment.strip().startswith('#') else '# ' + comment.strip()
                            lines[i] = f'{indent}{value_part}  {clean_comment}\n'
                        else:
                            lines[i] = f'{indent}{value_part}\n'
                        break
            
        # 生成新配置内容
        new_content = ''.join(lines)
        
        # 验证语法
        try:
            compile(new_content, '<string>', 'exec')
        except SyntaxError as e:
            return jsonify({'success': False, 'message': f'配置文件语法错误: 第{e.lineno}行 - {e.msg}'}), 400
        
        # 保存到 config.py
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        return jsonify({'success': True, 'message': '配置已保存，请重启框架以应用更改'})
    
    @app.route('/api/config/save', methods=['POST'])
    def save_config():
        content = request.get_json().get('content', '')
        if not content:
            return jsonify({'success': False, 'message': '配置内容不能为空'})
        
        # 验证语法
        try:
            compile(content, '<string>', 'exec')
        except SyntaxError as e:
            return jsonify({'success': False, 'message': f'语法错误: 第{e.lineno}行'})
        
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.py')
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return jsonify({'success': True, 'message': '配置已保存'})
    
    @app.route('/api/config/finish', methods=['POST'])
    def finish_config():
        # 验证配置
        config = load_config_module()
        if not config:
            return jsonify({'success': False, 'message': '配置加载失败'})
        
        appid = str(getattr(config, 'appid', '')).strip()
        secret = str(getattr(config, 'secret', '')).strip()
        
        if not appid or not secret:
            return jsonify({'success': False, 'message': '提示：</strong> 请填写必填项，请提前创建好mysql数据库，频道图床和主人openid等无需填写，其他配置可使用默认值'})
        
        base_dir = os.path.dirname(os.path.abspath(__file__))
        main_first = os.path.join(base_dir, 'main-first.py')
        main_file = os.path.join(base_dir, 'main.py')
        
        if not os.path.exists(main_first):
            return jsonify({'success': False, 'message': 'main-first.py 不存在'})
        
        # 创建完成标记
        os.makedirs(os.path.join(base_dir, 'data'), exist_ok=True)
        with open(os.path.join(base_dir, 'data', '.config_completed'), 'w') as f:
            f.write('1')
        
        # 备份并替换
        shutil.copy2(main_file, os.path.join(base_dir, 'main-wizard.py.bak'))
        shutil.copy2(main_first, main_file)
        os.remove(main_first)
        
        # 创建重启脚本
        is_windows = platform.system().lower() == 'windows'
        restart_script = f'''import os, sys, time, subprocess, platform
time.sleep(2)
subprocess.run(['taskkill', '/PID', '{os.getpid()}', '/F'], check=False) if platform.system().lower() == 'windows' else None
time.sleep(1)
subprocess.Popen([sys.executable, r"{main_file}"], creationflags=subprocess.CREATE_NEW_CONSOLE if platform.system().lower() == 'windows' else 0, cwd=r"{base_dir}")
time.sleep(1)
os.remove(__file__)
'''
        
        restart_file = os.path.join(base_dir, 'wizard_restart.py')
        with open(restart_file, 'w') as f:
            f.write(restart_script)
        
        # 启动重启脚本并退出
        subprocess.Popen([sys.executable, restart_file], cwd=base_dir,
                       creationflags=subprocess.CREATE_NEW_CONSOLE if is_windows else 0)
        threading.Timer(1.0, lambda: os._exit(0)).start()
        
        return jsonify({'success': True, 'message': '配置完成！正在重启...'})
    
    logging.basicConfig(level=logging.INFO, format='[配置向导] %(message)s')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    
    # 从配置文件读取端口号
    config = load_config_module()
    server_config = getattr(config, 'SERVER_CONFIG', {}) if config else {}
    wizard_port = server_config.get('port', 5003)
    wizard_host = server_config.get('host', '0.0.0.0')
    display_host = 'localhost' if wizard_host == '0.0.0.0' else wizard_host
    
    print(f"\\n✅ 配置向导已启动！")
    print(f"📋 请访问: http://{display_host}:{wizard_port}/web/")
    print("="*60 + "\\n")
    
    from eventlet import wsgi
    wsgi.server(eventlet.listen((wizard_host, wizard_port)), app, log=None, log_output=False)

if __name__ == "__main__":
    if check_initial_config():
        start_initial_config_wizard()
    else:
        # 正常模式：切换到 main-first.py
        base_dir = os.path.dirname(os.path.abspath(__file__))
        main_first = os.path.join(base_dir, 'main-first.py')
        main_file = os.path.join(base_dir, 'main.py')
        
        if os.path.exists(main_first):
            shutil.copy2(main_file, os.path.join(base_dir, 'main-wizard.py.bak'))
            shutil.copy2(main_first, main_file)
            print("✅ 配置已完成，切换到正常模式")
            print("🔄 请重新运行: python main.py")
            sys.exit(0)
        else:
            print("❌ main-first.py 不存在！")
            sys.exit(1)
