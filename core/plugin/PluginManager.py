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
    SEND_DEFAULT_RESPONSE, OWNER_IDS, OWNER_ONLY_REPLY, MAINTENANCE_CONFIG,
    DEFAULT_RESPONSE_EXCLUDED_REGEX, DEFAULT_RESPONSE_EXCLUDED_BY_DEFAULT, ENABLE_WELCOME_MESSAGE
)
from core.plugin import owner_reply_template, default_reply_template, maintenance_template, group_only_template, welcome_template
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

    # ===== 插件加载相关 =====
    @classmethod
    def load_plugins(cls):
        """
        加载所有插件（主目录、system、example 热更新）。
        """
        global _last_plugin_gc_time
        script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        loaded_count = 0
        if not cls._non_example_plugins_loaded:
            cls._load_non_example_plugins(script_dir)
            cls._non_example_plugins_loaded = True
            loaded_count = len(cls._plugins)
        example_loaded = cls._check_and_load_example_plugins(script_dir)
        
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
        return loaded_count + example_loaded + main_module_loaded
        
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
        加载主 plugins 目录下除 example 的所有插件。
        system 目录加载所有 .py，其他目录只加载 main.py。
        """
        for item in os.listdir(os.path.join(script_dir, 'plugins')):
            if item != 'example' and os.path.isdir(os.path.join(script_dir, 'plugins', item)):
                plugin_dir = os.path.join(script_dir, 'plugins', item)
                if item == 'system':
                    cls._load_system_plugins(plugin_dir)
                else:
                    main_py = os.path.join(plugin_dir, 'main.py')
                    if os.path.exists(main_py):
                        cls._load_standard_plugin(main_py)

    @classmethod
    def _load_system_plugins(cls, system_dir):
        """
        加载 system 目录下所有插件文件（不热更新）。
        """
        if not os.path.exists(system_dir) or not os.path.isdir(system_dir):
            return
        py_files = [f for f in os.listdir(system_dir) if f.endswith('.py') and f != '__init__.py']
        for py_file in py_files:
            plugin_file = os.path.join(system_dir, py_file)
            plugin_name = os.path.splitext(py_file)[0]
            try:
                spec = importlib.util.spec_from_file_location(f"plugins.system.{plugin_name}", plugin_file)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    sys.modules[f"plugins.system.{plugin_name}"] = module
                    plugin_classes_found = False
                    plugin_load_results = []
                    for attr_name in dir(module):
                        if attr_name.startswith('__') or not hasattr(getattr(module, attr_name), '__class__'):
                            continue
                        attr = getattr(module, attr_name)
                        if isinstance(attr, type) and attr.__module__ == module.__name__:
                            try:
                                if hasattr(attr, 'get_regex_handlers'):
                                    plugin_classes_found = True
                                    attr._source_file = plugin_file
                                    attr._is_system = True
                                    handlers_count = cls.register_plugin(attr)
                                    plugin_load_results.append(f"{attr_name}(优先级:{getattr(attr, 'priority', 10)},处理器:{handlers_count})")
                            except Exception as e:
                                error_msg = f"系统插件类 {attr_name} 注册失败: {str(e)}"
                                plugin_load_results.append(f"{attr_name}(注册失败:{str(e)})")
                                add_error_log(error_msg, traceback.format_exc())
                    if plugin_classes_found:
                        add_framework_log(f"系统插件 {py_file} 加载成功: {', '.join(plugin_load_results)}")
                    else:
                        add_framework_log(f"系统插件 {py_file} 中未找到有效的插件类")
            except Exception as e:
                error_msg = f"系统插件 {py_file} 加载失败：{str(e)}"
                add_error_log(error_msg, traceback.format_exc())
                add_framework_log(error_msg)

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
    def _check_and_load_example_plugins(cls, script_dir):
        """
        检查并加载 example 目录下的插件文件（支持热更新）。
        """
        example_dir = os.path.join(script_dir, 'plugins', 'example')
        if not os.path.exists(example_dir) or not os.path.isdir(example_dir):
            return 0
        py_files = [f for f in os.listdir(example_dir) if f.endswith('.py') and f != '__init__.py']
        loaded_count = 0
        for py_file in py_files:
            file_path = os.path.join(example_dir, py_file)
            last_modified = os.path.getmtime(file_path)
            if file_path not in cls._file_last_modified or cls._file_last_modified[file_path] < last_modified:
                cls._file_last_modified[file_path] = last_modified
                loaded_count += cls._load_example_plugin_file(file_path)
        deleted_files = []
        for file_path in list(cls._file_last_modified.keys()):
            if not os.path.exists(file_path) or not file_path.startswith(example_dir):
                deleted_files.append(file_path)
        for file_path in deleted_files:
            removed_count = cls._unregister_file_plugins(file_path)
            del cls._file_last_modified[file_path]
            if removed_count > 0:
                add_framework_log(f"热更新：文件已删除 {os.path.basename(file_path)}，已注销{removed_count}个处理器")
            else:
                add_framework_log(f"热更新：文件已删除 {os.path.basename(file_path)}")
        return loaded_count

    @classmethod
    def _load_example_plugin_file(cls, plugin_file):
        """
        加载 example 目录下单个插件文件（支持热更新）。
        """
        plugin_name = os.path.basename(plugin_file)
        module_name = os.path.splitext(plugin_name)[0]
        loaded_count = 0
        try:
            cls._unregister_file_plugins(plugin_file)
            last_modified = os.path.getmtime(plugin_file)
            cls._file_last_modified[plugin_file] = last_modified
            old_module = None
            module_fullname = f"plugins.example.{module_name}"
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
                        add_framework_log(f"热更新: {plugin_name} 加载成功: {', '.join(plugin_load_results)}")
                    else:
                        add_framework_log(f"热更新: {plugin_name} 加载成功")
                else:
                    add_framework_log(f"热更新: {plugin_name} 中未找到有效的插件类")
        except Exception as e:
            error_msg = f"热更新: {plugin_name} 加载失败: {str(e)}"
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
            module_fullname = f"plugins.example.{module_name}"
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
        return MAINTENANCE_CONFIG.get('enabled', False)

    @classmethod
    def can_user_bypass_maintenance(cls, user_id):
        """
        检查用户是否可以在维护模式下使用命令。
        """
        if MAINTENANCE_CONFIG.get('allow_owner', True) and user_id in OWNER_IDS:
            return True
        return False

    @classmethod
    def enter_maintenance_mode(cls):
        """
        进入维护模式。
        """
        MAINTENANCE_CONFIG['enabled'] = True
        add_framework_log(f"系统已进入维护模式")

    @classmethod
    def exit_maintenance_mode(cls):
        """
        退出维护模式。
        """
        MAINTENANCE_CONFIG['enabled'] = False
        add_framework_log(f"系统已退出维护模式")

    # ===== 消息分发与权限相关 =====
    @classmethod
    def dispatch_message(cls, event):
        """
        匹配消息并触发处理函数。
        """
        if event.event_type == 'GROUP_ADD_ROBOT':
            add_plugin_log(f"检测到机器人被邀请进入群聊: group_id={event.group_id}, inviter_id={event.user_id}")
            if ENABLE_WELCOME_MESSAGE:
                welcome_template.send_reply(event)
            return True
        if cls.is_maintenance_mode():
            if event.content in ['开启维护', '关闭维护', '维护状态', '联系管理员']:
                pass
            elif not cls.can_user_bypass_maintenance(event.user_id):
                add_plugin_log(f"系统处于维护模式，拒绝用户 {event.user_id} 的请求：{event.content}")
                maintenance_template.send_reply(event)
                return True
            else:
                add_plugin_log(f"维护模式中，主人 {event.user_id} 的请求：{event.content} 被允许处理")
        original_content = event.content
        has_slash_prefix = original_content and original_content.startswith('/')
        matched = False
        is_owner_denied_found = False
        is_group_only_denied_found = False
        if has_slash_prefix:
            event.content = original_content[1:]
            matched_handlers, is_owner_denied, is_group_only_denied = cls._find_matched_handlers(event.content, event)
            is_owner_denied_found = is_owner_denied
            is_group_only_denied_found = is_group_only_denied
            if matched_handlers:
                matched = cls._process_message_with_handlers(event, matched_handlers, original_content)
            if not matched:
                event.content = original_content
        if not matched:
            matched_handlers, is_owner_denied, is_group_only_denied = cls._find_matched_handlers(event.content, event)
            is_owner_denied_found = is_owner_denied_found or is_owner_denied
            is_group_only_denied_found = is_group_only_denied_found or is_group_only_denied
            if matched_handlers:
                matched = cls._process_message_with_handlers(event, matched_handlers)
        if not matched:
            if is_group_only_denied_found:
                add_plugin_log(f"用户 {event.user_id} 尝试在非群聊环境使用群聊专用命令，已拒绝")
                group_only_template.send_reply(event)
                return True
            elif is_owner_denied_found and OWNER_ONLY_REPLY:
                add_plugin_log(f"用户 {event.user_id} 尝试使用主人专属命令，已拒绝")
                owner_reply_template.send_reply(event)
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
    def _find_matched_handlers(cls, event_content, event=None):
        """
        查找匹配内容的所有处理器，并按优先级排序。
        """
        matched_handlers = []
        is_owner_denied = False
        is_group_only_denied = False
        for pattern, handler_info in cls._regex_handlers.items():
            compiled_regex = cls._regex_cache.get(pattern)
            if not compiled_regex:
                try:
                    compiled_regex = re.compile(pattern)
                    cls._regex_cache[pattern] = compiled_regex
                except Exception:
                    continue
            match = compiled_regex.search(event_content)
            if match:
                plugin_class = handler_info.get('class')
                handler_name = handler_info.get('handler')
                owner_only = handler_info.get('owner_only', False)
                group_only = handler_info.get('group_only', False)
                if event:
                    if owner_only and event.user_id not in OWNER_IDS:
                        is_owner_denied = True
                        continue
                    if group_only and not cls._is_group_chat(event):
                        is_group_only_denied = True
                        continue
                priority = cls._plugins.get(plugin_class, 10)
                matched_handlers.append({
                    'pattern': pattern,
                    'match': match,
                    'plugin_class': plugin_class,
                    'handler_name': handler_name,
                    'priority': priority,
                    'owner_only': owner_only,
                    'group_only': group_only
                })
        if matched_handlers:
            matched_handlers.sort(key=lambda x: x['priority'])
        return matched_handlers, is_owner_denied, is_group_only_denied

    @classmethod
    def _process_message_with_handlers(cls, event, matched_handlers, original_content=None):
        """
        依次处理所有匹配的消息处理器。
        """
        matched = False
        if original_content is not None and matched_handlers:
            add_plugin_log(f"检测到前缀，插件 {matched_handlers[0]['plugin_class'].__name__} 处理为：{event.content} (原始消息：{original_content})")
        for handler in matched_handlers:
            plugin_class = handler['plugin_class']
            handler_name = handler['handler_name']
            match = handler['match']
            plugin_name = plugin_class.__name__
            owner_only = handler['owner_only']
            group_only = handler['group_only']
            if owner_only and event.user_id not in OWNER_IDS:
                add_plugin_log(f"用户 {event.user_id} 尝试访问主人专属命令 {plugin_name}.{handler_name}，已拒绝")
                if OWNER_ONLY_REPLY:
                    owner_reply_template.send_reply(event)
                matched = True
                continue
            if group_only and not cls._is_group_chat(event):
                add_plugin_log(f"用户 {event.user_id} 尝试在非群聊环境使用群聊专用命令 {plugin_name}.{handler_name}，已拒绝")
                group_only_template.send_reply(event)
                matched = True
                continue
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
        """
        should_exclude = DEFAULT_RESPONSE_EXCLUDED_BY_DEFAULT
        is_match_found = False
        for regex_pattern in DEFAULT_RESPONSE_EXCLUDED_REGEX:
            try:
                if re.search(regex_pattern, content):
                    is_match_found = True
                    add_plugin_log(f"未匹配消息 '{content}' 匹配排除正则: '{regex_pattern}'")
                    break
            except Exception as e:
                add_error_log(f"排除正则 '{regex_pattern}' 匹配错误: {str(e)}")
        if DEFAULT_RESPONSE_EXCLUDED_BY_DEFAULT:
            should_exclude = not is_match_found
        else:
            should_exclude = is_match_found
        return should_exclude

    @classmethod
    def send_default_response(cls, event):
        """
        发送默认回复。
        """
        default_reply_template.send_reply(event) 