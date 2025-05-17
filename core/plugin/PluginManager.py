#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import re
import glob
import importlib.util
import sys
import traceback
import time

# 导入配置
from config import SEND_DEFAULT_RESPONSE, OWNER_IDS, OWNER_ONLY_REPLY

# 导入回复模板
from core.plugin import owner_reply_template, default_reply_template

# 导入Web面板日志功能
from web_panel.app import add_plugin_log, add_framework_log

class Plugin:
    """插件接口"""
    # 插件默认优先级（数字越小，优先级越高）
    priority = 10
    
    @staticmethod
    def get_regex_handlers():
        """
        注册正则规则与处理函数
        @return dict: {正则表达式: 处理函数名} 或 {正则表达式: {'handler': 处理函数名, 'owner_only': 是否仅主人可用}}
        owner_only 为 True 时，仅允许主人触发
        """
        raise NotImplementedError("子类必须实现get_regex_handlers方法")

class PluginManager:
    """插件管理器"""
    # 存储所有已注册的正则处理器
    _regex_handlers = {}
    
    # 存储已注册的插件类及其优先级
    _plugins = {}
    
    # 记录非热更新插件是否已加载
    _non_example_plugins_loaded = False
    
    # 存储热更新文件的最后修改时间
    _file_last_modified = {}
    
    @classmethod
    def load_plugins(cls):
        """加载所有插件，按照不同策略处理example和其他插件"""
        script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        # 1. 处理非example插件（只在首次加载）
        if not cls._non_example_plugins_loaded:
            cls._load_non_example_plugins(script_dir)
            cls._non_example_plugins_loaded = True
            
        # 2. 处理example目录的插件（只在文件变更时热更新）
        cls._check_and_load_example_plugins(script_dir)
    
    @classmethod
    def _load_non_example_plugins(cls, script_dir):
        """加载非example目录下的插件（只加载main.py）"""
        # 获取所有插件目录下的main.py文件（排除example目录）
        plugin_files = []
        for item in os.listdir(os.path.join(script_dir, 'plugins')):
            if item != 'example' and os.path.isdir(os.path.join(script_dir, 'plugins', item)):
                main_py = os.path.join(script_dir, 'plugins', item, 'main.py')
                if os.path.exists(main_py):
                    plugin_files.append(main_py)
        
        for plugin_file in plugin_files:
            # 获取插件名称
            plugin_name = os.path.basename(os.path.dirname(plugin_file))
            class_name = f"{plugin_name}_plugin"
            
            try:
                # 动态导入模块
                spec = importlib.util.spec_from_file_location(f"plugins.{plugin_name}.main", plugin_file)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    sys.modules[f"plugins.{plugin_name}.main"] = module
                    
                    # 检查插件类是否存在
                    if hasattr(module, class_name):
                        plugin_class = getattr(module, class_name)
                        cls.register_plugin(plugin_class)
                        add_framework_log(f"非热更新插件 {plugin_name} 加载成功")
                    else:
                        add_framework_log(f"插件 {plugin_name} 加载失败：未找到插件类 {class_name}")
            except Exception as e:
                error_msg = f"插件 {plugin_name} 加载失败：{str(e)}\n{traceback.format_exc()}"
                add_framework_log(error_msg)
    
    @classmethod
    def _check_and_load_example_plugins(cls, script_dir):
        """检查并加载变更的example目录下的插件文件"""
        example_dir = os.path.join(script_dir, 'plugins', 'example')
        if not os.path.exists(example_dir) or not os.path.isdir(example_dir):
            return
            
        py_files = [f for f in os.listdir(example_dir) if f.endswith('.py') and f != '__init__.py']
        files_changed = False
        
        # 检查文件是否有变更
        for py_file in py_files:
            file_path = os.path.join(example_dir, py_file)
            last_modified = os.path.getmtime(file_path)
            
            # 检查文件是否是新文件或已被修改
            if file_path not in cls._file_last_modified or cls._file_last_modified[file_path] < last_modified:
                cls._file_last_modified[file_path] = last_modified
                cls._load_example_plugin_file(file_path)
                files_changed = True
                add_framework_log(f"热更新：检测到文件 {py_file} 已更新，重新加载")
        
        # 如果有文件变更，清除不再存在的文件记录
        if files_changed:
            # 清理已删除文件的记录
            for file_path in list(cls._file_last_modified.keys()):
                if not os.path.exists(file_path) or not file_path.startswith(example_dir):
                    del cls._file_last_modified[file_path]
                    add_framework_log(f"热更新：文件已删除 {os.path.basename(file_path)}")
    
    @classmethod
    def _load_example_plugin_file(cls, plugin_file):
        """加载单个example插件文件"""
        # 清除该文件之前注册的处理器和插件
        cls._unregister_file_plugins(plugin_file)
        
        # 获取模块名（不含.py后缀）
        module_name = os.path.splitext(os.path.basename(plugin_file))[0]
        
        try:
            # 动态导入模块
            spec = importlib.util.spec_from_file_location(f"plugins.example.{module_name}", plugin_file)
            if spec and spec.loader:
                # 强制重新加载模块
                if f"plugins.example.{module_name}" in sys.modules:
                    del sys.modules[f"plugins.example.{module_name}"]
                    
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                sys.modules[f"plugins.example.{module_name}"] = module
                
                # 搜索模块中所有以_plugin结尾的类
                plugin_classes_found = False
                for attr_name in dir(module):
                    if attr_name.endswith('_plugin') and not attr_name.startswith('__'):
                        plugin_classes_found = True
                        plugin_class = getattr(module, attr_name)
                        
                        if hasattr(plugin_class, 'get_regex_handlers'):
                            try:
                                # 保存插件类对应的文件路径
                                plugin_class._source_file = plugin_file
                                cls.register_plugin(plugin_class)
                                add_framework_log(f"热更新插件 {attr_name} 从 {os.path.basename(plugin_file)} 加载成功")
                            except Exception as e:
                                add_framework_log(f"热更新插件 {attr_name} 注册失败: {str(e)}")
                
                if not plugin_classes_found:
                    add_framework_log(f"热更新：文件 {os.path.basename(plugin_file)} 中未找到插件类")
        except Exception as e:
            error_msg = f"热更新模块 {os.path.basename(plugin_file)} 加载失败：{str(e)}\n{traceback.format_exc()}"
            add_framework_log(error_msg)
    
    @classmethod
    def _unregister_file_plugins(cls, plugin_file):
        """注销指定文件中的所有插件"""
        removed = []
        # 遍历所有处理器和插件，移除来自该文件的插件
        plugin_classes_to_remove = []
        
        # 移除正则处理器
        for pattern, handler_info in list(cls._regex_handlers.items()):
            plugin_class = handler_info.get('class') if isinstance(handler_info, dict) else handler_info[0]
            if hasattr(plugin_class, '_source_file') and plugin_class._source_file == plugin_file:
                removed.append((plugin_class.__name__, pattern))
                del cls._regex_handlers[pattern]
                if plugin_class not in plugin_classes_to_remove:
                    plugin_classes_to_remove.append(plugin_class)
                
        # 移除插件类记录
        for plugin_class in plugin_classes_to_remove:
            if plugin_class in cls._plugins:
                del cls._plugins[plugin_class]
        
        if removed:
            add_framework_log(f"热更新：注销了 {len(removed)} 个来自 {os.path.basename(plugin_file)} 的处理器")
    
    @classmethod
    def register_plugin(cls, plugin_class):
        """注册插件"""
        # 获取插件优先级
        priority = getattr(plugin_class, 'priority', 10)  # 默认优先级为10
        
        # 记录插件及其优先级
        cls._plugins[plugin_class] = priority
        
        handlers = plugin_class.get_regex_handlers()
        
        for pattern, handler_info in handlers.items():
            # 解析处理器信息，支持两种格式：
            # 1. pattern: handler_name
            # 2. pattern: {'handler': handler_name, 'owner_only': 是否仅主人可用}
            if isinstance(handler_info, str):
                handler_name = handler_info
                owner_only = False  # 默认所有人可用
            else:
                handler_name = handler_info.get('handler')
                owner_only = handler_info.get('owner_only', False)
                
                # 兼容旧版本
                if 'priority' in handler_info:
                    # 忽略指令级优先级，使用警告提示
                    add_framework_log(f"警告：'{pattern}'指令设置了优先级，但现在优先级应该设置在插件类级别")
            
            # 注册插件处理器
            cls._regex_handlers[pattern] = {
                'class': plugin_class,
                'handler': handler_name,
                'owner_only': owner_only,
            }
            
            log_message = f"注册插件处理器：{pattern} -> {handler_name}"
            if owner_only:
                log_message += "，主人专属"
            add_framework_log(log_message)
        
        add_framework_log(f"插件 {plugin_class.__name__} 已注册，优先级：{priority}")
    
    @classmethod
    def dispatch_message(cls, event):
        """匹配消息并触发处理函数"""
        # 原始消息内容
        original_content = event.content
        
        # 检查消息是否以斜杠开头，如果是，创建一个去掉斜杠的版本
        has_slash_prefix = original_content and original_content.startswith('/')
        if has_slash_prefix:
            # 暂时修改event.content去掉斜杠前缀，用于匹配
            event.content = original_content[1:]
            # 此处先不记录日志，等确认有匹配的处理器后再记录
        
        # 尝试匹配消息
        matched = False
        
        # 收集所有匹配的插件处理器
        matched_handlers = []
        
        for pattern, handler_info in cls._regex_handlers.items():
            match = re.search(pattern, event.content)
            if match:
                plugin_class = handler_info.get('class') if isinstance(handler_info, dict) else handler_info[0]
                handler_name = handler_info.get('handler') if isinstance(handler_info, dict) else handler_info[1]
                owner_only = handler_info.get('owner_only', False) if isinstance(handler_info, dict) else False
                
                # 获取插件优先级
                priority = cls._plugins.get(plugin_class, 10)
                
                matched_handlers.append({
                    'pattern': pattern,
                    'match': match,
                    'plugin_class': plugin_class,
                    'handler_name': handler_name,
                    'priority': priority,
                    'owner_only': owner_only
                })
        
        # 按插件优先级排序匹配的处理器
        if matched_handlers:
            # 如果有匹配且使用了斜杠前缀，这时才记录日志
            if has_slash_prefix:
                add_plugin_log(f"检测到斜杠前缀，处理为: {event.content}")
                
            matched_handlers.sort(key=lambda x: x['priority'])
            
            # 依次处理匹配的处理器
            for handler in matched_handlers:
                plugin_class = handler['plugin_class']
                handler_name = handler['handler_name']
                owner_only = handler['owner_only']
                match = handler['match']
                
                # 检查主人权限
                if owner_only and event.user_id not in OWNER_IDS:
                    add_plugin_log(f"用户 {event.user_id} 尝试访问主人专属命令 {plugin_class.__name__}.{handler_name}，已拒绝")
                    if OWNER_ONLY_REPLY:
                        # 使用模板发送无权限回复
                        owner_reply_template.send_reply(event)
                    matched = True  # 命中了处理器，即使被拒绝
                    continue
                
                try:
                    event.matches = match.groups()
                    plugin_name = plugin_class.__name__
                    
                    # 记录开始处理消息
                    add_plugin_log(f"插件 {plugin_name} 开始处理消息：{event.content}")
                    
                    # 保存原始的reply方法
                    original_reply = event.reply
                    # 跟踪是否为首次回复
                    is_first_reply = [True]
                    
                    # 添加代理reply方法以记录回复内容
                    def reply_with_log(content, *args, **kwargs):
                        # 只记录文本内容，不管是否有按钮参数
                        text_content = content if isinstance(content, str) else "[非文本内容]"
                        
                        # 根据是否为首次回复添加不同标记
                        if is_first_reply[0]:
                            add_plugin_log(f"插件 {plugin_name} 回复：{text_content} (处理完成)")
                            is_first_reply[0] = False
                        else:
                            add_plugin_log(f"插件 {plugin_name} 回复：{text_content} (继续处理)")
                            
                        # 调用原始的reply方法
                        return original_reply(content, *args, **kwargs)
                    
                    # 替换reply方法
                    event.reply = reply_with_log
                    
                    # 执行插件处理函数
                    result = getattr(plugin_class, handler_name)(event)
                    
                    # 恢复原始的reply方法
                    event.reply = original_reply
                    
                    matched = True
                    
                    # 检查插件返回值，如果返回True则继续处理下一个匹配的处理器，否则终止处理
                    if result is not True:
                        break
                except Exception as e:
                    error_msg = f"插件 {plugin_class.__name__} 处理消息时出错：{str(e)}\n{traceback.format_exc()}"
                    add_plugin_log(error_msg)
                    matched = True
                    break
        
        # 如果没有找到匹配，且使用了无斜杠版本，则恢复原始消息内容再尝试匹配一次
        if not matched and event.content != original_content:
            # 恢复原始内容
            event.content = original_content
            
            # 重新收集所有匹配的处理器
            matched_handlers = []
            
            for pattern, handler_info in cls._regex_handlers.items():
                match = re.search(pattern, event.content)
                if match:
                    plugin_class = handler_info.get('class') if isinstance(handler_info, dict) else handler_info[0]
                    handler_name = handler_info.get('handler') if isinstance(handler_info, dict) else handler_info[1]
                    owner_only = handler_info.get('owner_only', False) if isinstance(handler_info, dict) else False
                    
                    # 获取插件优先级
                    priority = cls._plugins.get(plugin_class, 10)
                    
                    matched_handlers.append({
                        'pattern': pattern,
                        'match': match,
                        'plugin_class': plugin_class,
                        'handler_name': handler_name,
                        'priority': priority,
                        'owner_only': owner_only
                    })
            
            # 按插件优先级排序匹配的处理器
            if matched_handlers:
                matched_handlers.sort(key=lambda x: x['priority'])
                
                # 依次处理匹配的处理器
                for handler in matched_handlers:
                    plugin_class = handler['plugin_class']
                    handler_name = handler['handler_name']
                    owner_only = handler['owner_only']
                    match = handler['match']
                    
                    # 检查主人权限
                    if owner_only and event.user_id not in OWNER_IDS:
                        add_plugin_log(f"用户 {event.user_id} 尝试访问主人专属命令 {plugin_class.__name__}.{handler_name}，已拒绝")
                        if OWNER_ONLY_REPLY:
                            # 使用模板发送无权限回复
                            owner_reply_template.send_reply(event)
                        matched = True
                        continue
                    
                    try:
                        event.matches = match.groups()
                        plugin_name = plugin_class.__name__
                        
                        # 记录开始处理原始消息
                        add_plugin_log(f"插件 {plugin_name} 开始处理原始消息：{event.content}")
                        
                        # 保存原始的reply方法
                        original_reply = event.reply
                        # 跟踪是否为首次回复
                        is_first_reply = [True]
                        
                        # 添加代理reply方法以记录回复内容
                        def reply_with_log(content, *args, **kwargs):
                            # 只记录文本内容，不管是否有按钮参数
                            text_content = content if isinstance(content, str) else "[非文本内容]"
                            
                            # 根据是否为首次回复添加不同标记
                            if is_first_reply[0]:
                                add_plugin_log(f"插件 {plugin_name} 回复：{text_content} (处理完成)")
                                is_first_reply[0] = False
                            else:
                                add_plugin_log(f"插件 {plugin_name} 回复：{text_content} (处理完成)")
                                
                            # 调用原始的reply方法
                            return original_reply(content, *args, **kwargs)
                        
                        # 替换reply方法
                        event.reply = reply_with_log
                        
                        # 执行插件处理函数
                        result = getattr(plugin_class, handler_name)(event)
                        
                        # 恢复原始的reply方法
                        event.reply = original_reply
                        
                        matched = True
                        
                        # 检查插件返回值，如果返回True则继续处理下一个匹配的处理器，否则终止处理
                        if result is not True:
                            break
                    except Exception as e:
                        error_msg = f"插件 {plugin_class.__name__} 处理消息时出错：{str(e)}\n{traceback.format_exc()}"
                        add_plugin_log(error_msg)
                        matched = True
                        break
        
        # 确保event.content始终是原始内容
        event.content = original_content
        
        # 如果没有匹配到任何插件且配置了发送默认回复
        if not matched and SEND_DEFAULT_RESPONSE:
            add_plugin_log("未匹配到任何插件，发送默认回复")
            cls.send_default_response(event)
        
        return matched  # 是否有匹配的规则
    
    @classmethod
    def send_default_response(cls, event):
        """发送默认回复"""
        try:
            # 使用默认回复模板
            default_reply_template.send_reply(event)
            add_plugin_log("默认回复已发送")
        except Exception as e:
            error_msg = f"发送默认回复时出错：{str(e)}\n{traceback.format_exc()}"
            add_plugin_log(error_msg) 