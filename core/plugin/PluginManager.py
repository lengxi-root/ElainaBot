#!/usr/bin/env python
# -*- coding: utf-8 -*-

# ===== 1. 标准库导入 =====
import os
import re
import importlib.util
import sys
import traceback
import time
import gc  # 垃圾回收
import weakref

# ===== 2. 第三方库导入 =====
# （本文件暂未用到第三方库）

# ===== 3. 自定义模块导入 =====
from config import (
    SEND_DEFAULT_RESPONSE, OWNER_IDS, MAINTENANCE_MODE,
    DEFAULT_RESPONSE_EXCLUDED_REGEX, ENABLE_WELCOME_MESSAGE
)
from core.plugin.message_templates import MessageTemplate, MSG_TYPE_WELCOME, MSG_TYPE_MAINTENANCE, MSG_TYPE_GROUP_ONLY, MSG_TYPE_OWNER_ONLY, MSG_TYPE_DEFAULT
from web_panel.app import add_plugin_log, add_framework_log, add_error_log

# ===== 4. 全局变量与常量 =====
_last_plugin_gc_time = 0  # 上次插件垃圾回收时间
_plugin_gc_interval = 30  # 垃圾回收间隔(秒)

# ===== 5. 插件接口类 =====
class Plugin:
    """
    插件接口基类，所有插件需继承并实现 get_regex_handlers 方法。
    """
    priority = 10  # 插件默认优先级（数字越小，优先级越高）
    import_from_main = False  # 是否导入主模块插件实例

    @staticmethod
    def get_regex_handlers():
        """
        注册正则规则与处理函数
        @return dict: {正则表达式: 处理函数名} 或 {正则表达式: {'handler': 处理函数名, 'owner_only': 是否仅主人可用}}
        """
        raise NotImplementedError("子类必须实现get_regex_handlers方法")

# ===== 6. 插件管理器主类 =====
class PluginManager:
    """
    插件管理器，负责插件的加载、注册、分发、热更新、权限校验等。
    """
    _regex_handlers = {}      # 正则处理器表
    _plugins = {}            # 插件类及优先级
    _non_example_plugins_loaded = False
    _file_last_modified = {} # 热更新插件文件修改时间
    _unloaded_modules = []   # 待回收模块
    _regex_cache = {}        # 预编译正则缓存
    _hot_reload_dirs = ['example', 'system', 'alone']  # 热加载目录列表

    # ===== 插件加载相关 =====
    @classmethod
    def load_plugins(cls):
        """
        加载所有插件（主目录、system、alone、example 热更新）。
        """
        global _last_plugin_gc_time
        script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        loaded_count = 0
        
        # 创建热更新目录（如果不存在）
        for hot_dir in cls._hot_reload_dirs:
            hot_dir_path = os.path.join(script_dir, 'plugins', hot_dir)
            if not os.path.exists(hot_dir_path):
                try:
                    os.makedirs(hot_dir_path)
                    add_framework_log(f"创建热更新目录: {hot_dir}")
                except Exception as e:
                    add_error_log(f"创建热更新目录 {hot_dir} 失败: {str(e)}")
        
        if not cls._non_example_plugins_loaded:
            cls._load_non_example_plugins(script_dir)
            cls._non_example_plugins_loaded = True
            loaded_count = len(cls._plugins)
            
        # 加载所有热更新目录的插件
        hot_reload_loaded = 0
        for hot_dir in cls._hot_reload_dirs:
            hot_reload_loaded += cls._check_and_load_hot_reload_plugins(script_dir, hot_dir)
        
        # 导入主模块插件实例
        main_module_loaded = cls._import_main_module_instances()
        
        # 定期垃圾回收
        current_time = time.time()
        if cls._unloaded_modules and (current_time - _last_plugin_gc_time >= _plugin_gc_interval):
            for module in cls._unloaded_modules:
                for attr_name in dir(module):
                    if not attr_name.startswith('__'):
                        try:
                            delattr(module, attr_name)
                        except:
                            pass
            cls._unloaded_modules.clear()
            gc.collect()
            _last_plugin_gc_time = current_time
        return loaded_count + hot_reload_loaded + main_module_loaded
        
    @classmethod
    def _import_main_module_instances(cls):
        """
        从主模块导入插件类实例并注册它们的处理器。
        """
        loaded_count = 0
        for plugin_class in list(cls._plugins.keys()):
            if hasattr(plugin_class, 'import_from_main') and plugin_class.import_from_main:
                try:
                    # 找到主模块实例
                    module_name = plugin_class.__module__
                    if module_name.startswith('plugins.') and '.main' in module_name:
                        try:
                            module = sys.modules.get(module_name)
                            if not module:
                                continue
                                
                            # 查找插件实例
                            for attr_name in dir(module):
                                if attr_name.startswith('__') or not hasattr(getattr(module, attr_name), '__class__'):
                                    continue
                                    
                                attr = getattr(module, attr_name)
                                # 检查是否为插件实例（非类型）
                                if not isinstance(attr, type) and hasattr(attr, 'get_regex_handlers'):
                                    # 如果是插件实例，注册其处理器
                                    instance_name = f"{plugin_class.__name__}.{attr_name}"
                                    handlers = cls._register_instance_handlers(plugin_class, attr)
                                    if handlers > 0:
                                        loaded_count += 1
                                        add_framework_log(f"从主模块导入插件实例 {instance_name} 处理器成功，共 {handlers} 个")
                                    
                        except Exception as e:
                            error_msg = f"导入主模块实例处理器失败: {str(e)}"
                            add_error_log(error_msg, traceback.format_exc())
                            add_framework_log(error_msg)
                except Exception as e:
                    add_error_log(f"检查导入主模块配置失败: {str(e)}", traceback.format_exc())
        
        return loaded_count

    @classmethod
    def _register_instance_handlers(cls, plugin_class, instance):
        """
        注册插件实例的处理器到主插件类。
        返回注册的处理器数量。
        """
        try:
            handlers = instance.get_regex_handlers()
            if not handlers:
                return 0
                
            handlers_count = 0
            for pattern, handler_info in handlers.items():
                if isinstance(handler_info, str):
                    method_name = handler_info
                    owner_only = False
                    group_only = False
                else:
                    method_name = handler_info.get('handler')
                    owner_only = handler_info.get('owner_only', False)
                    group_only = handler_info.get('group_only', False)
                
                # 确保方法存在于实例中
                if not hasattr(instance, method_name):
                    add_framework_log(f"警告：实例没有处理方法 {method_name}")
                    continue
                
                # 创建闭包函数，将实例方法绑定到插件类静态方法
                def create_handler(inst, method):
                    def handler_method(event):
                        return getattr(inst, method)(event)
                    return handler_method
                
                # 为防止命名冲突，创建唯一方法名
                unique_method_name = f"_instance_handler_{handlers_count}_{method_name}"
                setattr(plugin_class, unique_method_name, create_handler(instance, method_name))
                
                # 向插件类添加处理器
                if pattern.startswith('^'):
                    enhanced_pattern = pattern
                else:
                    enhanced_pattern = f"^{pattern}"
                
                try:
                    compiled_regex = re.compile(enhanced_pattern)
                    cls._regex_cache[enhanced_pattern] = compiled_regex
                except Exception as e:
                    add_error_log(f"实例处理器正则表达式 '{enhanced_pattern}' 编译失败: {str(e)}")
                    continue
                
                cls._regex_handlers[enhanced_pattern] = {
                    'class': plugin_class,
                    'handler': unique_method_name,
                    'owner_only': owner_only,
                    'group_only': group_only,
                    'original_pattern': pattern,
                    'from_instance': True,
                    'instance_method': method_name
                }
                handlers_count += 1
                
            return handlers_count
        except Exception as e:
            add_error_log(f"注册实例处理器失败: {str(e)}", traceback.format_exc())
            return 0

    @classmethod
    def _load_non_example_plugins(cls, script_dir):
        """
        加载主 plugins 目录下除热更新目录外的所有标准插件。
        只加载main.py文件。
        """
        for item in os.listdir(os.path.join(script_dir, 'plugins')):
            if item not in cls._hot_reload_dirs and os.path.isdir(os.path.join(script_dir, 'plugins', item)):
                plugin_dir = os.path.join(script_dir, 'plugins', item)
                main_py = os.path.join(plugin_dir, 'main.py')
                if os.path.exists(main_py):
                    cls._load_standard_plugin(main_py)

    @classmethod
    def _load_standard_plugin(cls, plugin_file):
        """
        加载标准插件（main.py）。
        """
        plugin_name = os.path.basename(os.path.dirname(plugin_file))
        class_name = f"{plugin_name}_plugin"
        try:
            spec = importlib.util.spec_from_file_location(f"plugins.{plugin_name}.main", plugin_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                sys.modules[f"plugins.{plugin_name}.main"] = module
                if hasattr(module, class_name):
                    plugin_class = getattr(module, class_name)
                    plugin_class._source_file = plugin_file
                    plugin_class._is_standard = True
                    cls.register_plugin(plugin_class)
                    add_framework_log(f"标准插件 {plugin_name} 加载成功")
                else:
                    error_msg = f"插件 {plugin_name} 加载失败：未找到插件类 {class_name}"
                    add_error_log(error_msg)
                    add_framework_log(error_msg)
        except Exception as e:
            error_msg = f"插件 {plugin_name} 加载失败：{str(e)}"
            add_error_log(error_msg, traceback.format_exc())
            add_framework_log(error_msg)

    @classmethod
    def _check_and_load_hot_reload_plugins(cls, script_dir, dir_name):
        """
        检查并加载指定热更新目录下的插件文件（支持热更新）。
        """
        hot_dir = os.path.join(script_dir, 'plugins', dir_name)
        if not os.path.exists(hot_dir) or not os.path.isdir(hot_dir):
            return 0
            
        py_files = [f for f in os.listdir(hot_dir) if f.endswith('.py') and f != '__init__.py']
        loaded_count = 0
        
        # 记录当前目录下的所有插件文件的绝对路径
        current_files = {os.path.join(hot_dir, py_file) for py_file in py_files}
        
        # 加载或更新现有文件
        for py_file in py_files:
            file_path = os.path.join(hot_dir, py_file)
            last_modified = os.path.getmtime(file_path)
            if file_path not in cls._file_last_modified or cls._file_last_modified[file_path] < last_modified:
                cls._file_last_modified[file_path] = last_modified
                loaded_count += cls._load_hot_reload_plugin_file(file_path, dir_name)
                
        # 处理已删除的文件（仅处理当前目录内不存在的文件）
        dir_files_to_delete = []
        for file_path in list(cls._file_last_modified.keys()):
            # 只处理当前热更新目录下不存在的文件
            if file_path.startswith(hot_dir) and file_path not in current_files:
                dir_files_to_delete.append(file_path)
                
        for file_path in dir_files_to_delete:
            removed_count = cls._unregister_file_plugins(file_path)
            del cls._file_last_modified[file_path]
            add_framework_log(f"热更新：检测到文件已删除 {dir_name}/{os.path.basename(file_path)}，已注销 {removed_count} 个处理器")
                
        return loaded_count

    @classmethod
    def _load_hot_reload_plugin_file(cls, plugin_file, dir_name):
        """
        加载热更新目录下单个插件文件。
        """
        plugin_name = os.path.basename(plugin_file)
        module_name = os.path.splitext(plugin_name)[0]
        loaded_count = 0
        
        try:
            cls._unregister_file_plugins(plugin_file)
            last_modified = os.path.getmtime(plugin_file)
            cls._file_last_modified[plugin_file] = last_modified
            old_module = None
            module_fullname = f"plugins.{dir_name}.{module_name}"
            
            if module_fullname in sys.modules:
                old_module = sys.modules[module_fullname]
                del sys.modules[module_fullname]
                
            spec = importlib.util.spec_from_file_location(module_fullname, plugin_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_fullname] = module
                
                try:
                    spec.loader.exec_module(module)
                except Exception as e:
                    if old_module:
                        sys.modules[module_fullname] = old_module
                    else:
                        del sys.modules[module_fullname]
                    raise e
                    
                if old_module:
                    cls._unloaded_modules.append(old_module)
                    
                plugin_load_results = []
                plugin_classes_found = False
                
                for attr_name in dir(module):
                    if attr_name.startswith('__') or not hasattr(getattr(module, attr_name), '__class__'):
                        continue
                        
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and attr.__module__ == module.__name__:
                        try:
                            if hasattr(attr, 'get_regex_handlers'):
                                plugin_classes_found = True
                                loaded_count += 1
                                attr._source_file = plugin_file
                                attr._is_hot_reload = True
                                handlers_count = cls.register_plugin(attr)
                                priority = getattr(attr, 'priority', 10)
                                plugin_load_results.append(f"{attr_name}(优先级:{priority},处理器:{handlers_count})")
                        except Exception as e:
                            error_msg = f"插件类 {attr_name} 注册失败: {str(e)}"
                            plugin_load_results.append(f"{attr_name}(注册失败:{str(e)})")
                            add_error_log(error_msg, traceback.format_exc())
                            
                if plugin_classes_found:
                    if plugin_load_results:
                        add_framework_log(f"热更新: {dir_name}/{plugin_name} 加载成功: {', '.join(plugin_load_results)}")
                    else:
                        add_framework_log(f"热更新: {dir_name}/{plugin_name} 加载成功")
                else:
                    add_framework_log(f"热更新: {dir_name}/{plugin_name} 中未找到有效的插件类")
        except Exception as e:
            error_msg = f"热更新: {dir_name}/{plugin_name} 加载失败: {str(e)}"
            add_error_log(error_msg, traceback.format_exc())
            add_framework_log(error_msg)
            
        return loaded_count

    @classmethod
    def _unregister_file_plugins(cls, plugin_file):
        """
        注销指定文件中的所有插件，返回注销的处理器数量。
        """
        global _last_plugin_gc_time
        removed = []
        plugin_classes_to_remove = []
        
        for pattern, handler_info in list(cls._regex_handlers.items()):
            plugin_class = handler_info.get('class') if isinstance(handler_info, dict) else handler_info[0]
            if hasattr(plugin_class, '_source_file') and plugin_class._source_file == plugin_file:
                removed.append((plugin_class.__name__, pattern))
                del cls._regex_handlers[pattern]
                if pattern in cls._regex_cache:
                    del cls._regex_cache[pattern]
                if plugin_class not in plugin_classes_to_remove:
                    plugin_classes_to_remove.append(plugin_class)
                    
        for plugin_class in plugin_classes_to_remove:
            if plugin_class in cls._plugins:
                del cls._plugins[plugin_class]
                
        if plugin_file and os.path.exists(plugin_file):
            module_name = os.path.splitext(os.path.basename(plugin_file))[0]
            dir_name = os.path.basename(os.path.dirname(plugin_file))
            module_fullname = f"plugins.{dir_name}.{module_name}"
            if module_fullname in sys.modules:
                cls._unloaded_modules.append(sys.modules[module_fullname])
                
        current_time = time.time()
        if removed and (current_time - _last_plugin_gc_time >= _plugin_gc_interval):
            gc.collect()
            _last_plugin_gc_time = current_time
            
        return len(removed)

    # ===== 插件注册与分发相关 =====
    @classmethod
    def register_plugin(cls, plugin_class, skip_log=False):
        """
        注册插件，返回注册的处理器数量。
        """
        priority = getattr(plugin_class, 'priority', 10)
        cls._plugins[plugin_class] = priority
        handlers = plugin_class.get_regex_handlers()
        handlers_info = []
        handlers_count = 0
        for pattern, handler_info in handlers.items():
            if isinstance(handler_info, str):
                handler_name = handler_info
                owner_only = False
                group_only = False
            else:
                handler_name = handler_info.get('handler')
                owner_only = handler_info.get('owner_only', False)
                group_only = handler_info.get('group_only', False)
                if 'priority' in handler_info and not skip_log:
                    add_framework_log(f"警告：'{pattern}'指令设置了优先级，但现在优先级应该设置在插件类级别")
            if pattern.startswith('^'):
                enhanced_pattern = pattern
            else:
                enhanced_pattern = f"^{pattern}"
            try:
                compiled_regex = re.compile(enhanced_pattern)
                cls._regex_cache[enhanced_pattern] = compiled_regex
            except Exception as e:
                add_error_log(f"正则表达式 '{enhanced_pattern}' 编译失败: {str(e)}")
                continue
            cls._regex_handlers[enhanced_pattern] = {
                'class': plugin_class,
                'handler': handler_name,
                'owner_only': owner_only,
                'group_only': group_only,
                'original_pattern': pattern
            }
            handler_text = f"{enhanced_pattern} -> {handler_name}"
            if owner_only:
                handler_text += "(主人专属)"
            if group_only:
                handler_text += "(仅群聊)"
            handlers_info.append(handler_text)
            handlers_count += 1
        if not skip_log:
            if handlers_info:
                handlers_text = "，".join(handlers_info)
                add_framework_log(f"插件 {plugin_class.__name__} 已注册(优先级:{priority})，处理器：{handlers_text}")
            else:
                add_framework_log(f"插件 {plugin_class.__name__} 已注册(优先级:{priority})，无处理器")
        return handlers_count

    @classmethod
    def is_maintenance_mode(cls):
        """
        检查是否在维护模式。
        """
        return MAINTENANCE_MODE

    @classmethod
    def can_user_bypass_maintenance(cls, user_id):
        """
        检查用户是否可以在维护模式下使用命令。
        主人始终可以在维护模式下使用命令。
        """
        return user_id in OWNER_IDS

    @classmethod
    def enter_maintenance_mode(cls):
        """
        进入维护模式。
        """
        global MAINTENANCE_MODE
        MAINTENANCE_MODE = True
        add_framework_log(f"系统已进入维护模式")

    @classmethod
    def exit_maintenance_mode(cls):
        """
        退出维护模式。
        """
        global MAINTENANCE_MODE
        MAINTENANCE_MODE = False
        add_framework_log(f"系统已退出维护模式")

    # ===== 消息分发与权限相关 =====
    @classmethod
    def dispatch_message(cls, event):
        """
        消息分发处理主入口
        @param event: 事件对象
        @return: 是否被处理
        """
        try:
            # 0. 检查消息是否已被处理（如在MessageEvent中直接处理的消息）
            if hasattr(event, 'handled') and event.handled:
                return True
                
            # 1. 维护模式检查 - 快速判断
            if cls.is_maintenance_mode() and not cls.can_user_bypass_maintenance(event.user_id):
                add_plugin_log(f"系统处于维护模式，拒绝用户 {event.user_id} 的请求")
                MessageTemplate.send(event, MSG_TYPE_MAINTENANCE)
                return True
                
            # 2. 确定用户环境，提前获取这些状态，避免重复判断
            is_owner = event.user_id in OWNER_IDS
            is_group = cls._is_group_chat(event)
            
            # 3. 处理消息并获取匹配结果
            result = cls._process_message(event, is_owner, is_group)
            
            return result
        except Exception as e:
            error_msg = f"消息分发处理失败: {str(e)}"
            add_error_log(error_msg, traceback.format_exc())
            add_framework_log(error_msg)
            return False

    @classmethod
    def _process_message(cls, event, is_owner, is_group):
        """
        处理消息并匹配处理器
        @param event: 消息事件对象
        @param is_owner: 是否为主人
        @param is_group: 是否在群聊中
        @return: 是否被处理
        """
        original_content = event.content
        matched = False
        
        # 检查斜杠前缀，按照优先级处理
        has_slash_prefix = original_content and original_content.startswith('/')
        
        # 存储权限拒绝情况
        permission_denied = {
            'owner_denied': False, 
            'group_denied': False
        }
        
        # 1. 尝试处理带斜杠前缀的命令
        if has_slash_prefix:
            event.content = original_content[1:]
            matched_handlers = cls._find_matched_handlers(
                event.content, event, is_owner, is_group, permission_denied
            )
            if matched_handlers:
                matched = cls._execute_handlers(event, matched_handlers, original_content)
            if not matched:
                event.content = original_content
                
        # 2. 处理不带斜杠的原始命令
        if not matched:
            matched_handlers = cls._find_matched_handlers(
                event.content, event, is_owner, is_group, permission_denied
            )
            if matched_handlers:
                matched = cls._execute_handlers(event, matched_handlers)
                
        # 3. 如果没有匹配到任何处理器，处理权限拒绝或发送默认回复
        if not matched:
            if permission_denied['group_denied']:
                add_plugin_log(f"用户 {event.user_id} 尝试在非群聊环境使用群聊专用命令，已拒绝")
                MessageTemplate.send(event, MSG_TYPE_GROUP_ONLY)
                return True
            elif permission_denied['owner_denied']:
                add_plugin_log(f"用户 {event.user_id} 尝试使用主人专属命令，已拒绝")
                MessageTemplate.send(event, MSG_TYPE_OWNER_ONLY)
                return True
            elif SEND_DEFAULT_RESPONSE:
                should_exclude = cls._should_exclude_default_response(event.content)
                if not should_exclude:
                    add_plugin_log("未匹配到任何插件，发送默认回复")
                    cls.send_default_response(event)
                else:
                    add_plugin_log("未匹配到任何插件，但根据配置不发送默认回复")
                    
        return matched

    @classmethod
    def _find_matched_handlers(cls, event_content, event, is_owner, is_group, permission_denied=None):
        """
        查找匹配内容的所有处理器，并按优先级排序。
        同时检查权限条件，过滤掉不符合条件的处理器
        
        @param event_content: 消息内容
        @param event: 消息事件对象
        @param is_owner: 是否为主人
        @param is_group: 是否在群聊中
        @param permission_denied: 权限拒绝信息记录
        @return: 满足所有条件并按优先级排序的处理器列表
        """
        matched_handlers = []
        
        for pattern, handler_info in cls._regex_handlers.items():
            # 1. 编译并匹配正则
            compiled_regex = cls._regex_cache.get(pattern)
            if not compiled_regex:
                try:
                    compiled_regex = re.compile(pattern)
                    cls._regex_cache[pattern] = compiled_regex
                except Exception:
                    continue
                    
            match = compiled_regex.search(event_content)
            if not match:
                continue
            
            # 2. 获取处理器信息
            plugin_class = handler_info.get('class')
            handler_name = handler_info.get('handler')
            owner_only = handler_info.get('owner_only', False)
            group_only = handler_info.get('group_only', False)
            
            # 3. 检查权限条件
            # 主人权限检查
            if owner_only and not is_owner:
                if permission_denied is not None:
                    permission_denied['owner_denied'] = True
                continue
                
            # 群聊限制检查
            if group_only and not is_group:
                if permission_denied is not None:
                    permission_denied['group_denied'] = True
                continue
                
            # 4. 添加通过所有检查的处理器
            priority = cls._plugins.get(plugin_class, 10)
            matched_handlers.append({
                'pattern': pattern,
                'match': match,
                'plugin_class': plugin_class,
                'handler_name': handler_name,
                'priority': priority
            })
        
        # 5. 按优先级排序
        if matched_handlers:
            matched_handlers.sort(key=lambda x: x['priority'])
            
        return matched_handlers

    @classmethod
    def _execute_handlers(cls, event, matched_handlers, original_content=None):
        """
        执行匹配的处理器
        @param event: 消息事件对象
        @param matched_handlers: 匹配的处理器列表
        @param original_content: 原始消息内容
        @return: 是否成功处理
        """
        matched = False
        
        if original_content is not None and matched_handlers:
            add_plugin_log(f"检测到前缀，插件 {matched_handlers[0]['plugin_class'].__name__} 处理为：{event.content} (原始消息：{original_content})")
            
        for handler in matched_handlers:
            plugin_class = handler['plugin_class']
            handler_name = handler['handler_name']
            match = handler['match']
            plugin_name = plugin_class.__name__
            
            try:
                event.matches = match.groups()
                if original_content is None:
                    add_plugin_log(f"插件 {plugin_name} 开始处理消息：{event.content}")
                    
                result = cls._call_plugin_handler_with_logging(plugin_class, handler_name, event, plugin_name)
                matched = True
                
                if result is not True:
                    break
            except Exception as e:
                error_msg = f"插件 {plugin_class.__name__} 处理消息时出错：{str(e)}"
                stack_trace = traceback.format_exc()
                add_plugin_log(error_msg)
                add_error_log(error_msg, stack_trace)
                matched = True
                break
                
        return matched

    @classmethod
    def _is_group_chat(cls, event):
        """
        判断消息事件是否来自群聊。
        """
        if event.event_type == "GROUP_AT_MESSAGE_CREATE":
            return True
        elif event.event_type == "AT_MESSAGE_CREATE":
            return True
        elif event.event_type == "INTERACTION_CREATE":
            chat_type = event.get('d/chat_type') if hasattr(event, 'get') else None
            if chat_type is not None:
                return chat_type == 1
            scene = event.get('d/scene') if hasattr(event, 'get') else None
            if scene is not None:
                return scene == 'group'
            if hasattr(event, 'is_group'):
                return event.is_group
            if hasattr(event, 'group_id') and event.group_id and event.group_id != "c2c":
                return True
            return False
        elif hasattr(event, 'is_group'):
            return event.is_group
        elif hasattr(event, 'group_id') and event.group_id and event.group_id != "c2c":
            return True
        return False

    @classmethod
    def _call_plugin_handler_with_logging(cls, plugin_class, handler_name, event, plugin_name):
        """
        调用插件处理函数并处理日志记录。
        """
        original_reply = event.reply
        is_first_reply = [True]
        def reply_with_log(content, *args, **kwargs):
            text_content = content if isinstance(content, str) else "[非文本内容]"
            if is_first_reply[0]:
                add_plugin_log(f"插件 {plugin_name} 回复：{text_content} (处理完成)")
                is_first_reply[0] = False
            else:
                add_plugin_log(f"插件 {plugin_name} 回复：{text_content} (继续处理)")
            return original_reply(content, *args, **kwargs)
        event.reply = reply_with_log
        try:
            result = getattr(plugin_class, handler_name)(event)
            return result
        finally:
            event.reply = original_reply

    # ===== 默认回复相关 =====
    @classmethod
    def _should_exclude_default_response(cls, content):
        """
        检查是否应该排除默认回复。
        只使用黑名单模式：匹配排除正则的内容不发送默认回复。
        """
        for regex_pattern in DEFAULT_RESPONSE_EXCLUDED_REGEX:
            try:
                if re.search(regex_pattern, content):
                    add_plugin_log(f"未匹配消息 '{content}' 匹配排除正则: '{regex_pattern}'")
                    return True
            except Exception as e:
                add_error_log(f"排除正则 '{regex_pattern}' 匹配错误: {str(e)}")
        return False

    @classmethod
    def send_default_response(cls, event):
        """
        发送默认回复。
        """
        MessageTemplate.send(event, MSG_TYPE_DEFAULT) 