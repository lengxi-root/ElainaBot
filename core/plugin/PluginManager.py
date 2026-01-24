#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import re
import importlib.util
import sys
import traceback
import time
import gc
import weakref
import asyncio
import json
import logging
import threading
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor

from config import (
    SEND_DEFAULT_RESPONSE, OWNER_IDS, MAINTENANCE_MODE,
    DEFAULT_RESPONSE_EXCLUDED_REGEX
)
from core.plugin.message_templates import MessageTemplate, MSG_TYPE_MAINTENANCE, MSG_TYPE_GROUP_ONLY, MSG_TYPE_OWNER_ONLY, MSG_TYPE_DEFAULT, MSG_TYPE_BLACKLIST, MSG_TYPE_GROUP_BLACKLIST
from web.app import add_plugin_log
from function.log_db import add_log_to_db, add_framework_log, add_error_log

_logger = logging.getLogger('ElainaBot.core.PluginManager')

_TIMESTAMP_FORMAT = '%Y-%m-%d %H:%M:%S'
_ID_COMMAND_PATTERN = re.compile(r'^/?我的id$')
_LOG_TYPE_ERROR = 'error'
_GROUP_EVENT_TYPES = frozenset(['GROUP_AT_MESSAGE_CREATE', 'AT_MESSAGE_CREATE'])
_INTERACTION_EVENT = 'INTERACTION_CREATE'
_DEFAULT_GROUP_ID = 'c2c'
_BLACKLIST_RELOAD_INTERVAL = 60
_OWNER_IDS_SET = frozenset(OWNER_IDS)

_maintenance_mode_enabled = MAINTENANCE_MODE
_send_default_response = SEND_DEFAULT_RESPONSE

try:
    from config import BLACKLIST_ENABLED
    _blacklist_enabled = BLACKLIST_ENABLED
except ImportError:
    _blacklist_enabled = False

try:
    from config import GROUP_BLACKLIST_ENABLED
    _group_blacklist_enabled = GROUP_BLACKLIST_ENABLED
except ImportError:
    _group_blacklist_enabled = False

def _log_error(error_msg, error_trace=None):
    if error_trace:
        _logger.error(f"{error_msg}\n{error_trace}")
    else:
        _logger.error(error_msg)
    try:
        add_error_log(error_msg, error_trace or "")
    except:
        pass

_base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_data_dir = os.path.join(_base_dir, "data")
_blacklist_file = os.path.join(_data_dir, "blacklist.json")
_group_blacklist_file = os.path.join(_data_dir, "group_blacklist.json")

_blacklist_cache = {}
_blacklist_last_load = 0
_group_blacklist_cache = {}
_group_blacklist_last_load = 0

_last_plugin_gc_time = 0
_plugin_gc_interval = 30
_last_quick_check_time = 0
_plugins_loaded = False
_last_cache_cleanup = 0
_plugin_executor = ThreadPoolExecutor(max_workers=100, thread_name_prefix="PluginWorker")

_SOFT_TIMEOUT = 3.0
_HARD_TIMEOUT = 300.0
_background_tasks = {}
_background_tasks_lock = threading.Lock()
_last_background_cleanup = 0

class Plugin:
    priority = 10
    import_from_main = False

    @staticmethod
    def get_regex_handlers():
        raise NotImplementedError("子类必须实现get_regex_handlers方法")

@lru_cache(maxsize=256)
def _compile_regex_cached(pattern):
    try:
        return re.compile(pattern, re.DOTALL)
    except Exception:
        return None

class PluginManager:
    _regex_handlers = {}
    _plugins = {}
    _file_last_modified = {}
    _unloaded_modules = []
    _sorted_handlers = []
    _handler_patterns_cache = {}
    _web_routes = {}
    _api_routes = {}
    _csp_domains = {}  # 存储插件的CSP域名配置
    _exclude_patterns_cache = None
    _message_interceptors = []  # 消息拦截器列表

    @staticmethod
    def _get_event_info(event):
        return (
            getattr(event, 'user_id', ''),
            getattr(event, 'group_id', None) or _DEFAULT_GROUP_ID,
            getattr(event, 'content', '')
        )
    
    @classmethod
    def _safe_execute(cls, func, error_msg_template, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            _log_error(error_msg_template.format(error=str(e)), traceback.format_exc())
            return None
    
    @classmethod
    def _extract_module_info(cls, file_path):
        if not file_path:
            return "未知目录", "未知模块", "unknown"
        dir_name = os.path.basename(os.path.dirname(file_path))
        module_name = os.path.splitext(os.path.basename(file_path))[0]
        return dir_name, module_name, f"plugins.{dir_name}.{module_name}"
    
    @classmethod
    def _check_permissions(cls, handler_info, is_owner, is_group):
        owner_only = handler_info.get('owner_only', False)
        group_only = handler_info.get('group_only', False)
        if owner_only and not is_owner:
            return False, 'owner_denied'
        if group_only and not is_group:
            return False, 'group_denied'
        return True, None
    
    @classmethod
    def _cleanup_resources(cls, obj, context=""):
        for method_name in ['cleanup', 'close', 'shutdown']:
            if hasattr(obj, method_name) and callable(getattr(obj, method_name)):
                try:
                    method = getattr(obj, method_name)
                    if method_name == 'shutdown' and hasattr(method, '__code__') and 'wait' in method.__code__.co_varnames:
                        method(wait=False)
                    else:
                        method()
                    if context:
                        add_framework_log(f"{context}：执行清理方法 {method_name}")
                    return True
                except Exception:
                    pass
        return False
    
    @classmethod
    def _enhance_pattern(cls, pattern):
        return pattern if pattern.startswith('^') else f"^{pattern}"
    
    @classmethod
    def _compile_and_cache_regex(cls, pattern, error_context=""):
        compiled_regex = _compile_regex_cached(pattern)
        if not compiled_regex and error_context:
            _log_error(f"{error_context}正则表达式 '{pattern}' 编译失败")
        return compiled_regex

    @classmethod
    def _load_json_cache(cls, file_path, cache, last_load, enabled):
        if not enabled:
            return {}, last_load
        
        if not os.path.exists(_data_dir):
            os.makedirs(_data_dir, exist_ok=True)
        if not os.path.exists(file_path):
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump({}, f)
        
        current_time = time.time()
        if last_load == 0 or (current_time - last_load > _BLACKLIST_RELOAD_INTERVAL):
            try:
                mtime = os.path.getmtime(file_path)
                if not cache or mtime > last_load:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        return json.load(f), current_time
            except Exception as e:
                _log_error(f"加载缓存文件失败 {file_path}: {str(e)}")
        return cache, last_load
    
    @classmethod
    def load_blacklist(cls):
        global _blacklist_cache, _blacklist_last_load
        _blacklist_cache, _blacklist_last_load = cls._load_json_cache(
            _blacklist_file, _blacklist_cache, _blacklist_last_load, _blacklist_enabled
        )
        return _blacklist_cache
    
    @classmethod
    def load_group_blacklist(cls):
        global _group_blacklist_cache, _group_blacklist_last_load
        _group_blacklist_cache, _group_blacklist_last_load = cls._load_json_cache(
            _group_blacklist_file, _group_blacklist_cache, _group_blacklist_last_load, _group_blacklist_enabled
        )
        return _group_blacklist_cache
    
    @classmethod
    def is_group_blacklisted(cls, group_id):
        if not _group_blacklist_enabled or not group_id:
            return False, ""
        blacklist = cls.load_group_blacklist()
        if group_id in blacklist:
            return True, blacklist.get(group_id, "未指明原因")
        return False, ""
    
    @classmethod
    def is_blacklisted(cls, user_id):
        if not _blacklist_enabled or not user_id:
            return False, ""
        blacklist = cls.load_blacklist()
        if str(user_id) in blacklist:
            return True, blacklist[str(user_id)]
        return False, ""

    @classmethod
    def reload_plugin(cls, plugin_class):
        try:
            if not hasattr(plugin_class, '_source_file'):
                add_error_log(f"插件类 {plugin_class.__name__} 没有 _source_file 属性", "")
                return False
            
            file_path = plugin_class._source_file
            if not os.path.exists(file_path):
                add_error_log(f"插件文件不存在: {file_path}", "")
                return False
            
            dir_name, module_name, _ = cls._extract_module_info(file_path)
            add_framework_log(f"插件热加载: {dir_name}/{module_name}.py")
            
            loaded_count = cls._load_plugin_file(file_path, dir_name)
            success = loaded_count > 0
            add_framework_log(f"插件热加载{'成功' if success else '失败'}: {dir_name}/{module_name}.py")
            return success
        except Exception as e:
            _log_error(f"插件热加载失败: {str(e)}", traceback.format_exc())
            return False
    
    @classmethod
    def load_plugins(cls):
        global _last_plugin_gc_time, _last_quick_check_time, _plugins_loaded, _last_cache_cleanup
        
        current_time = time.time()
        if _plugins_loaded and current_time - _last_quick_check_time < 2:
            return len(cls._plugins)
        
        _last_quick_check_time = current_time
        
        script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        plugins_dir = os.path.join(script_dir, 'plugins')
        
        if not os.path.exists(plugins_dir):
            os.makedirs(plugins_dir, exist_ok=True)
            return 0
        
        cls._cleanup_deleted_files()
        
        loaded_count = 0
        for dir_name in os.listdir(plugins_dir):
            dir_path = os.path.join(plugins_dir, dir_name)
            if os.path.isdir(dir_path):
                loaded_count += cls._load_plugins_from_directory(script_dir, dir_name)
        
        main_module_loaded = cls._import_main_module_instances()
        cls._periodic_gc()
        
        if current_time - _last_cache_cleanup > 300:
            if len(cls._handler_patterns_cache) > 200:
                cls._handler_patterns_cache.clear()
            _last_cache_cleanup = current_time
        
        _plugins_loaded = True
        return loaded_count + main_module_loaded
    
    @classmethod
    def _cleanup_deleted_files(cls):
        deleted_files = [fp for fp in cls._file_last_modified.keys() if not os.path.exists(fp)]
        for file_path in deleted_files:
            dir_name, module_name, _ = cls._extract_module_info(file_path)
            removed_count = cls._unregister_file_plugins(file_path)
            if file_path in cls._file_last_modified:
                del cls._file_last_modified[file_path]
            if removed_count > 0:
                add_framework_log(f"文件已删除 {dir_name}/{module_name}.py，注销 {removed_count} 个处理器")
    
    @classmethod
    def _periodic_gc(cls):
        global _last_plugin_gc_time
        current_time = time.time()
        
        if cls._unloaded_modules and (current_time - _last_plugin_gc_time >= _plugin_gc_interval):
            try:
                for module in cls._unloaded_modules[:]:
                    try:
                        for attr_name in list(dir(module)):
                            if not attr_name.startswith('__'):
                                try:
                                    delattr(module, attr_name)
                                except:
                                    pass
                        del module
                    except:
                        pass
                cls._unloaded_modules.clear()
                gc.collect()
            except:
                pass
            finally:
                _last_plugin_gc_time = current_time
        
    @classmethod
    def _import_main_module_instances(cls):
        loaded_count = 0
        for plugin_class in list(cls._plugins.keys()):
            if hasattr(plugin_class, 'import_from_main') and plugin_class.import_from_main:
                try:
                    module_name = plugin_class.__module__
                    if module_name.startswith('plugins.'):
                        module = sys.modules.get(module_name)
                        if module:
                            loaded_count += cls._register_module_instances(plugin_class, module)
                except Exception as e:
                    _log_error(f"导入主模块实例失败: {str(e)}", traceback.format_exc())
        return loaded_count
    
    @classmethod
    def _register_module_instances(cls, plugin_class, module):
        loaded_count = 0
        for attr_name in dir(module):
            if attr_name.startswith('__'):
                continue
            try:
                attr = getattr(module, attr_name)
                if not isinstance(attr, type) and hasattr(attr, 'get_regex_handlers'):
                    handlers = cls._register_instance_handlers(plugin_class, attr)
                    if handlers > 0:
                        loaded_count += 1
            except Exception as e:
                error_msg = f"注册实例处理器失败: {attr_name} - {str(e)}"
                _log_error(error_msg, traceback.format_exc())
                try:
                    add_log_to_db(_LOG_TYPE_ERROR, {
                        'timestamp': time.strftime(_TIMESTAMP_FORMAT),
                        'plugin_name': plugin_class.__name__,
                        'instance_name': attr_name,
                        'content': error_msg,
                        'traceback': traceback.format_exc()
                    })
                except:
                    pass
        return loaded_count

    @classmethod
    def _register_instance_handlers(cls, plugin_class, instance):
        def _register_handlers():
            handlers = instance.get_regex_handlers()
            if not handlers:
                return 0
                
            handlers_count = 0
            for pattern, handler_info in handlers.items():
                if isinstance(handler_info, str):
                    method_name = handler_info
                    owner_only = group_only = False
                else:
                    method_name = handler_info.get('handler')
                    owner_only = handler_info.get('owner_only', False)
                    group_only = handler_info.get('group_only', False)
                
                if not hasattr(instance, method_name):
                    continue
                
                # 创建处理器闭包
                def create_handler(inst, method):
                    def handler_method(event):
                        return getattr(inst, method)(event)
                    return handler_method
                
                unique_method_name = f"_instance_handler_{handlers_count}_{method_name}"
                setattr(plugin_class, unique_method_name, create_handler(instance, method_name))
                
                enhanced_pattern = cls._enhance_pattern(pattern)
                compiled_regex = cls._compile_and_cache_regex(enhanced_pattern, "实例处理器")
                if not compiled_regex:
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
        
        return cls._safe_execute(_register_handlers, "注册实例处理器失败: {error}") or 0

    @classmethod
    def _load_plugins_from_directory(cls, script_dir, dir_name):
        plugin_dir = os.path.join(script_dir, 'plugins', dir_name)
        if not os.path.exists(plugin_dir) or not os.path.isdir(plugin_dir):
            cls._unregister_directory_plugins(plugin_dir)
            return 0
            
        py_files = [f for f in os.listdir(plugin_dir) if f.endswith('.py') and f != '__init__.py']
        loaded_count = 0
        
        current_files = {os.path.join(plugin_dir, py_file) for py_file in py_files}
        cls._cleanup_directory_deleted_files(plugin_dir, current_files, dir_name)
        
        for py_file in py_files:
            file_path = os.path.join(plugin_dir, py_file)
            if os.path.exists(file_path):
                try:
                    last_modified = os.path.getmtime(file_path)
                    if (file_path not in cls._file_last_modified or 
                        cls._file_last_modified[file_path] < last_modified):
                        cls._file_last_modified[file_path] = last_modified
                        loaded_count += cls._load_plugin_file(file_path, dir_name)
                except (OSError, IOError) as e:
                    _log_error(f"获取文件修改时间失败: {file_path}, 错误: {str(e)}")
                
        return loaded_count
    
    @classmethod
    def _cleanup_directory_deleted_files(cls, plugin_dir, current_files, dir_name):
        dir_files_to_delete = []
        for file_path in list(cls._file_last_modified.keys()):
            if (file_path.startswith(plugin_dir) and 
                (file_path not in current_files or not os.path.exists(file_path))):
                dir_files_to_delete.append(file_path)
        
        for file_path in dir_files_to_delete:
            removed_count = cls._unregister_file_plugins(file_path)
            if file_path in cls._file_last_modified:
                del cls._file_last_modified[file_path]
            module_name = os.path.splitext(os.path.basename(file_path))[0]
            add_framework_log(f"文件已删除 {dir_name}/{module_name}.py，注销 {removed_count} 个处理器")
        
    @classmethod
    def _unregister_directory_plugins(cls, plugin_dir):
        removed_count = 0
        dir_files_to_delete = [fp for fp in cls._file_last_modified.keys() 
                              if fp.startswith(plugin_dir)]
        
        for file_path in dir_files_to_delete:
            removed_count += cls._unregister_file_plugins(file_path)
            if file_path in cls._file_last_modified:
                del cls._file_last_modified[file_path]
        
        if removed_count > 0:
            dir_name = os.path.basename(plugin_dir)
            add_framework_log(f"目录已删除 {dir_name}，注销 {removed_count} 个处理器")
            
        return removed_count

    @classmethod
    def _load_plugin_file(cls, plugin_file, dir_name):
        dir_name, module_name, module_fullname = cls._extract_module_info(plugin_file)
        plugin_name = os.path.basename(plugin_file)
        loaded_count = 0
        
        try:
            is_hot_reload = module_fullname in sys.modules
            
            cls._unregister_file_plugins(plugin_file)
            
            last_modified = os.path.getmtime(plugin_file)
            cls._file_last_modified[plugin_file] = last_modified
            
            old_module = sys.modules.get(module_fullname)
            if old_module:
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
                    
                loaded_count = cls._register_module_plugins(module, plugin_file, dir_name, plugin_name, is_hot_reload)
                    
        except Exception as e:
            error_msg = f"插件{'热更新' if module_fullname in sys.modules else '加载'}: {dir_name}/{plugin_name} 失败: {str(e)}"
            error_trace = traceback.format_exc()
            _log_error(error_msg, error_trace)
            
            # 记录插件加载失败到数据库
            try:
                add_log_to_db(_LOG_TYPE_ERROR, {
                    'timestamp': time.strftime(_TIMESTAMP_FORMAT),
                    'plugin_name': plugin_name,
                    'plugin_file': plugin_file,
                    'plugin_dir': dir_name,
                    'content': error_msg,
                    'traceback': error_trace
                })
            except:
                pass
            
        return loaded_count
    
    @classmethod
    def _register_module_plugins(cls, module, plugin_file, dir_name, plugin_name, is_hot_reload):
        loaded_count = 0
        plugin_load_results = []
        plugin_classes_found = False
        
        for attr_name in dir(module):
            if attr_name.startswith('__'):
                continue
                
            attr = getattr(module, attr_name)
            if (isinstance(attr, type) and 
                attr.__module__ == module.__name__ and
                hasattr(attr, 'get_regex_handlers')):
                
                try:
                    plugin_classes_found = True
                    loaded_count += 1
                    attr._source_file = plugin_file
                    attr._is_hot_reload = True
                    handlers_count = cls.register_plugin(attr)
                    priority = getattr(attr, 'priority', 10)
                    plugin_load_results.append(f"{attr_name}(优先级:{priority},处理器:{handlers_count})")
                except Exception as e:
                    error_msg = f"插件类 {attr_name} 注册失败: {str(e)}"
                    error_trace = traceback.format_exc()
                    plugin_load_results.append(f"{attr_name}(注册失败:{str(e)})")
                    _log_error(error_msg, error_trace)
                    
                    try:
                        add_log_to_db(_LOG_TYPE_ERROR, {
                            'timestamp': time.strftime(_TIMESTAMP_FORMAT),
                            'plugin_name': attr_name,
                            'plugin_file': plugin_file,
                            'content': error_msg,
                            'traceback': error_trace
                        })
                    except:
                        pass
                    
        if plugin_classes_found:
            status = "热更新" if is_hot_reload else "加载"
            if plugin_load_results:
                add_framework_log(f"插件{status}成功: {dir_name}/{plugin_name} - {', '.join(plugin_load_results)}")
            else:
                add_framework_log(f"插件{status}成功: {dir_name}/{plugin_name}")
        else:
            add_framework_log(f"插件{'热更新' if is_hot_reload else '加载'}: {dir_name}/{plugin_name} 中未找到有效的插件类")
            
        return loaded_count

    @classmethod
    def _unregister_file_plugins(cls, plugin_file):
        dir_name, module_name, module_fullname = cls._extract_module_info(plugin_file)
        removed = []
        plugin_classes_to_remove = []
        
        for pattern, handler_info in list(cls._regex_handlers.items()):
            try:
                plugin_class = handler_info.get('class') if isinstance(handler_info, dict) else handler_info[0]
                if hasattr(plugin_class, '_source_file') and plugin_class._source_file == plugin_file:
                    removed.append((plugin_class.__name__, pattern))
                    if plugin_class not in plugin_classes_to_remove:
                        plugin_classes_to_remove.append(plugin_class)
            except Exception as e:
                _log_error(f"查找插件类时出错: {str(e)}", traceback.format_exc())
        
        for pattern, handler_info in list(cls._regex_handlers.items()):
            try:
                plugin_class = handler_info.get('class') if isinstance(handler_info, dict) else handler_info[0]
                if plugin_class in plugin_classes_to_remove:
                    del cls._regex_handlers[pattern]
                    # LRU 缓存会自动管理，无需手动删除
            except Exception as e:
                _log_error(f"清理正则处理器时出错: {str(e)}", traceback.format_exc())
        
        for route_path, route_info in list(cls._web_routes.items()):
            try:
                plugin_class = route_info.get('class')
                if plugin_class in plugin_classes_to_remove:
                    del cls._web_routes[route_path]
                    add_framework_log(f"注销Web路由: {route_path}")
            except Exception as e:
                _log_error(f"清理Web路由时出错: {str(e)}", traceback.format_exc())
        
        for api_path, api_info in list(cls._api_routes.items()):
            try:
                plugin_class = api_info.get('class')
                if plugin_class in plugin_classes_to_remove:
                    del cls._api_routes[api_path]
                    add_framework_log(f"注销API路由: {api_path}")
            except Exception as e:
                _log_error(f"清理API路由时出错: {str(e)}", traceback.format_exc())
        
        for plugin_class in plugin_classes_to_remove:
            cls._cleanup_plugin_class(plugin_class)
        
        if module_fullname != "unknown" and module_fullname in sys.modules:
            cls._cleanup_module(module_fullname, os.path.exists(plugin_file))
        
        if removed:
            cls._rebuild_sorted_handlers()
            cls._handler_patterns_cache.clear()
            
        return len(removed)
    
    @classmethod
    def _cleanup_plugin_class(cls, plugin_class):
        try:
            if plugin_class in cls._plugins:
                del cls._plugins[plugin_class]
            
            cls._cleanup_resources(plugin_class, f"插件清理：{plugin_class.__name__}")
            
            for attr_name in dir(plugin_class):
                if not attr_name.startswith('__'):
                    try:
                        attr = getattr(plugin_class, attr_name)
                        if not isinstance(attr, type):
                            cls._cleanup_resources(attr, f"插件清理：{plugin_class.__name__}.{attr_name}")
                    except Exception:
                        pass
                        
        except Exception as e:
            _log_error(f"清理插件类资源时出错: {str(e)}", traceback.format_exc())
    
    @classmethod
    def _cleanup_module(cls, module_fullname, file_exists):
        try:
            module = sys.modules[module_fullname]
            
            cls._cleanup_resources(module, f"模块清理：{module_fullname}")
            
            for attr_name in dir(module):
                if not attr_name.startswith('__'):
                    try:
                        attr = getattr(module, attr_name)
                        if not isinstance(attr, type):
                            cls._cleanup_resources(attr, f"模块清理：{module_fullname}.{attr_name}")
                    except Exception:
                        pass
            
            cls._unloaded_modules.append(module)
            
            if module_fullname in sys.modules:
                del sys.modules[module_fullname]
                
        except Exception as e:
            _log_error(f"清理模块时出错: {str(e)}", traceback.format_exc())

    @classmethod 
    def _rebuild_sorted_handlers(cls):
        handlers_with_priority = []
        
        for pattern, handler_info in cls._regex_handlers.items():
            plugin_class = handler_info.get('class')
            priority = cls._plugins.get(plugin_class, 10)
            
            handlers_with_priority.append({
                'pattern': pattern,
                'handler_info': handler_info,
                'priority': priority
            })
        
        cls._sorted_handlers = sorted(handlers_with_priority, key=lambda x: x['priority'])
    
    @classmethod
    def _rebuild_handler_patterns_cache(cls):
        cls._handler_patterns_cache.clear()
        
        for i, handler_data in enumerate(cls._sorted_handlers):
            pattern = handler_data['pattern']
            handler_info = handler_data['handler_info']
            priority = handler_data['priority']
            
            # 直接使用 LRU 缓存编译
            compiled_regex = cls._compile_and_cache_regex(pattern)
            if not compiled_regex:
                continue
            
            handler_key = f"{priority}_{i}_{pattern}"
            cls._handler_patterns_cache[handler_key] = {
                'regex': compiled_regex,
                'handler_info': handler_info,
                'priority': priority,
                'pattern': pattern
            }

    @classmethod
    def register_plugin(cls, plugin_class, skip_log=False):
        priority = getattr(plugin_class, 'priority', 10)
        cls._plugins[plugin_class] = priority
        handlers = plugin_class.get_regex_handlers()
        handlers_count = 0
        
        for pattern, handler_info in handlers.items():
            if isinstance(handler_info, str):
                handler_name = handler_info
                owner_only = group_only = False
            else:
                handler_name = handler_info.get('handler')
                owner_only = handler_info.get('owner_only', False)
                group_only = handler_info.get('group_only', False)
                
            enhanced_pattern = cls._enhance_pattern(pattern)
            compiled_regex = cls._compile_and_cache_regex(enhanced_pattern)
            if not compiled_regex:
                continue
                
            cls._regex_handlers[enhanced_pattern] = {
                'class': plugin_class,
                'handler': handler_name,
                'owner_only': owner_only,
                'group_only': group_only,
                'original_pattern': pattern
            }
            handlers_count += 1
        
        if hasattr(plugin_class, 'get_web_routes') and callable(getattr(plugin_class, 'get_web_routes')):
            try:
                web_route_info = plugin_class.get_web_routes()
                if web_route_info and isinstance(web_route_info, dict):
                    route_path = web_route_info.get('path')
                    if route_path:
                        cls._web_routes[route_path] = {
                            'class': plugin_class,
                            'menu_name': web_route_info.get('menu_name', route_path),
                            'menu_icon': web_route_info.get('menu_icon', 'bi-puzzle'),
                            'description': web_route_info.get('description', ''),
                            'handler': web_route_info.get('handler', 'render_page'),
                            'priority': web_route_info.get('priority', 100)
                        }
                        add_framework_log(f"插件 {plugin_class.__name__} 注册Web路由: {route_path}")
                        
                        # 收集CSP域名配置
                        csp_domains = web_route_info.get('csp_domains', {})
                        if csp_domains and isinstance(csp_domains, dict):
                            for directive, domains in csp_domains.items():
                                if directive not in cls._csp_domains:
                                    cls._csp_domains[directive] = set()
                                if isinstance(domains, (list, tuple, set)):
                                    cls._csp_domains[directive].update(domains)
                                elif isinstance(domains, str):
                                    cls._csp_domains[directive].add(domains)
                            add_framework_log(f"插件 {plugin_class.__name__} 注册CSP域名: {csp_domains}")
                        
                        api_routes = web_route_info.get('api_routes', [])
                        if api_routes and isinstance(api_routes, list):
                            for api_route in api_routes:
                                api_path = api_route.get('path')
                                if api_path:
                                    cls._api_routes[api_path] = {
                                        'class': plugin_class,
                                        'handler': api_route.get('handler'),
                                        'methods': api_route.get('methods', ['GET']),
                                        'require_auth': api_route.get('require_auth', True),
                                        'require_token': api_route.get('require_token', True)
                                    }
                                    add_framework_log(f"插件 {plugin_class.__name__} 注册API路由: {api_path}")
            except Exception as e:
                _log_error(f"注册Web路由失败: {plugin_class.__name__} - {str(e)}", traceback.format_exc())
        
        cls._rebuild_sorted_handlers()
        cls._handler_patterns_cache.clear()
        return handlers_count

    @classmethod
    def register_message_interceptor(cls, interceptor_func, priority=100, plugin_class=None):
        cls._message_interceptors.append({'func': interceptor_func, 'priority': priority, 'plugin_class': plugin_class})
        cls._message_interceptors.sort(key=lambda x: x['priority'])
        add_framework_log(f"注册消息拦截器: {interceptor_func.__name__} (优先级: {priority})")
        return True
    
    @classmethod
    def unregister_message_interceptor(cls, interceptor_func=None, plugin_class=None):
        original_count = len(cls._message_interceptors)
        if interceptor_func:
            cls._message_interceptors = [i for i in cls._message_interceptors if i['func'] != interceptor_func]
        if plugin_class:
            cls._message_interceptors = [i for i in cls._message_interceptors if i['plugin_class'] != plugin_class]
        removed = original_count - len(cls._message_interceptors)
        if removed:
            add_framework_log(f"注销了 {removed} 个消息拦截器")
        return removed
    
    @classmethod
    def get_message_interceptors(cls):
        return cls._message_interceptors.copy()
    
    @classmethod
    def call_message_interceptors(cls, message_info):
        for interceptor in cls._message_interceptors:
            try:
                result = interceptor['func'](message_info)
                if result is None or result is False:
                    add_framework_log(f"消息被拦截器 {interceptor['func'].__name__} 阻止")
                    return None
                if isinstance(result, dict):
                    message_info = result
            except Exception as e:
                _log_error(f"消息拦截器 {interceptor['func'].__name__} 执行失败: {str(e)}", traceback.format_exc())
        return message_info

    @classmethod
    def is_maintenance_mode(cls):
        global _maintenance_mode_enabled
        return _maintenance_mode_enabled

    @classmethod
    def can_user_bypass_maintenance(cls, user_id):
        return user_id in _OWNER_IDS_SET
    
    @classmethod
    def reload_config_status(cls):
        global _maintenance_mode_enabled, _blacklist_enabled, _group_blacklist_enabled, _send_default_response, _OWNER_IDS_SET
        try:
            import importlib
            import config as config_module
            importlib.reload(config_module)
            
            _maintenance_mode_enabled = config_module.MAINTENANCE_MODE
            _send_default_response = config_module.SEND_DEFAULT_RESPONSE
            _OWNER_IDS_SET = frozenset(config_module.OWNER_IDS)
            _blacklist_enabled = getattr(config_module, 'BLACKLIST_ENABLED', False)
            _group_blacklist_enabled = getattr(config_module, 'GROUP_BLACKLIST_ENABLED', False)
            
            if _group_blacklist_enabled:
                cls.load_group_blacklist()
            
            add_framework_log(f"配置已更新 - 维护:{_maintenance_mode_enabled}, 黑名单:{_blacklist_enabled}, 群黑名单:{_group_blacklist_enabled}")
            return True
        except Exception as e:
            _log_error(f"重新加载配置失败: {str(e)}", traceback.format_exc())
            return False

    @classmethod
    def dispatch_message(cls, event):
        try:
            global _maintenance_mode_enabled, _blacklist_enabled, _group_blacklist_enabled, _last_background_cleanup
            
            cls.load_plugins()
            
            current_time = time.time()
            if current_time - _last_background_cleanup > 30:
                cls._cleanup_background_tasks()
                _last_background_cleanup = current_time
            
            if getattr(event, 'handled', False):
                return True
            
            group_id = getattr(event, 'group_id', None)
            if _group_blacklist_enabled and group_id:
                is_blocked, _ = cls.is_group_blacklisted(group_id)
                if is_blocked:
                    MessageTemplate.send(event, MSG_TYPE_GROUP_BLACKLIST, group_id=group_id)
                    return True
            
            user_id = getattr(event, 'user_id', None)
            if _blacklist_enabled and user_id:
                is_blocked, reason = cls.is_blacklisted(user_id)
                if is_blocked:
                    content = getattr(event, 'content', '')
                    if not (content and _ID_COMMAND_PATTERN.match(content.strip())):
                        MessageTemplate.send(event, MSG_TYPE_BLACKLIST, reason=reason)
                        return True
            
            if _maintenance_mode_enabled and not cls.can_user_bypass_maintenance(user_id):
                MessageTemplate.send(event, MSG_TYPE_MAINTENANCE)
                return True
                
            is_owner = user_id in _OWNER_IDS_SET
            is_group = cls._is_group_chat(event)
            
            return cls._process_message(event, is_owner, is_group)
            
        except Exception as e:
            error_msg = f"消息分发处理失败: {str(e)}"
            error_trace = traceback.format_exc()
            _log_error(error_msg, error_trace)
            
            # 记录到数据库
            try:
                user_id, group_id, content = cls._get_event_info(event)
                add_log_to_db(_LOG_TYPE_ERROR, {
                    'timestamp': time.strftime(_TIMESTAMP_FORMAT),
                    'user_id': user_id,
                    'group_id': group_id,
                    'message_content': content[:500],
                    'content': error_msg,
                    'traceback': error_trace
                })
            except:
                pass
            
            return False

    @classmethod
    def _process_message(cls, event, is_owner, is_group):
        original_content = event.content
        matched = False
        
        has_slash_prefix = original_content and original_content.startswith('/')
        permission_denied = {'owner_denied': False, 'group_denied': False}
        
        if has_slash_prefix:
            event.content = original_content[1:]
            matched_handlers = cls._find_matched_handlers(
                event.content, event, is_owner, is_group, permission_denied
            )
            if matched_handlers:
                matched = cls._execute_handlers(event, matched_handlers, original_content)
            if not matched:
                event.content = original_content
                
        if not matched:
            matched_handlers = cls._find_matched_handlers(
                event.content, event, is_owner, is_group, permission_denied
            )
            if matched_handlers:
                matched = cls._execute_handlers(event, matched_handlers)
                
        if not matched:
            matched = cls._handle_unmatched_message(event, permission_denied, original_content)
                    
        return matched
    
    @classmethod
    def _handle_unmatched_message(cls, event, permission_denied, original_content):
        if permission_denied['group_denied']:
            MessageTemplate.send(event, MSG_TYPE_GROUP_ONLY)
            return True
        elif permission_denied['owner_denied']:
            MessageTemplate.send(event, MSG_TYPE_OWNER_ONLY)
            return True
        elif _send_default_response:
            should_exclude = cls._should_exclude_default_response(event.content)
            if not should_exclude:
                cls.send_default_response(event)
        
        return False

    @classmethod
    def _find_matched_handlers(cls, event_content, event, is_owner, is_group, permission_denied=None):
        matched_handlers = []
        
        if not cls._handler_patterns_cache:
            cls._rebuild_handler_patterns_cache()
        
        for handler_key, handler_cache in cls._handler_patterns_cache.items():
            compiled_regex = handler_cache['regex']
            handler_info = handler_cache['handler_info']
            priority = handler_cache['priority']
            pattern = handler_cache['pattern']
            
            match = compiled_regex.search(event_content)
            if not match:
                continue
            
            has_permission, deny_reason = cls._check_permissions(handler_info, is_owner, is_group)
            
            if not has_permission:
                if permission_denied is not None:
                    permission_denied[deny_reason] = True
                continue
                
            matched_handlers.append({
                'pattern': pattern,
                'match': match,
                'plugin_class': handler_info.get('class'),
                'handler_name': handler_info.get('handler'),
                'priority': priority
            })
            
        return matched_handlers

    @classmethod
    def _execute_handlers(cls, event, matched_handlers, original_content=None):
        matched = False
        
        for handler in matched_handlers:
            plugin_class = handler['plugin_class']
            handler_name = handler['handler_name']
            match = handler['match']
            plugin_name = plugin_class.__name__
            
            try:
                event.matches = match.groups()
                result = cls._call_plugin_handler_with_logging(plugin_class, handler_name, event, plugin_name)
                matched = True
                
                if result is not True:
                    break
            except Exception as e:
                error_msg = f"插件 {plugin_class.__name__} 处理消息时出错：{str(e)}"
                error_trace = traceback.format_exc()
                _log_error(error_msg, error_trace)
                
                # 记录详细的错误信息到数据库
                try:
                    user_id, group_id, content = cls._get_event_info(event)
                    add_log_to_db(_LOG_TYPE_ERROR, {
                        'timestamp': time.strftime(_TIMESTAMP_FORMAT),
                        'plugin_name': plugin_class.__name__,
                        'handler_name': handler_name,
                        'user_id': user_id,
                        'group_id': group_id,
                        'message_content': content[:500],
                        'content': error_msg,
                        'traceback': error_trace
                    })
                except:
                    pass
                    
                matched = True
                break
        
        return matched

    @classmethod
    def _is_group_chat(cls, event):
        if event.event_type in _GROUP_EVENT_TYPES:
            return True
        if event.event_type == _INTERACTION_EVENT:
            if hasattr(event, 'get'):
                chat_type = event.get('d/chat_type')
                if chat_type is not None:
                    return chat_type == 1
                scene = event.get('d/scene')
                if scene is not None:
                    return scene == 'group'
        if hasattr(event, 'is_group'):
            return event.is_group
        group_id = getattr(event, 'group_id', None)
        return bool(group_id and group_id != _DEFAULT_GROUP_ID)

    @classmethod
    def _call_plugin_handler_with_logging(cls, plugin_class, handler_name, event, plugin_name):
        global _plugin_executor
        
        original_methods = {
            'reply': event.reply,
            'reply_image': getattr(event, 'reply_image', None),
            'reply_voice': getattr(event, 'reply_voice', None),
            'reply_video': getattr(event, 'reply_video', None),
            'reply_ark': getattr(event, 'reply_ark', None),
            'reply_markdown': getattr(event, 'reply_markdown', None),
            'reply_md': getattr(event, 'reply_md', None),
            'reply_markdown_aj': getattr(event, 'reply_markdown_aj', None)
        }
        
        is_first_reply = [True]
        wrapped_methods = cls._create_method_logger(original_methods, plugin_name, is_first_reply, event)
        
        for method_name, wrapped_method in wrapped_methods.items():
            if wrapped_method:
                setattr(event, method_name, wrapped_method)
        
        try:
            handler = getattr(plugin_class, handler_name)
            
            def execute_handler():
                start_time = time.time()
                try:
                    result = handler(event)
                    if asyncio.iscoroutine(result):
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_closed():
                                raise RuntimeError()
                        except RuntimeError:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                        try:
                            result = loop.run_until_complete(result)
                        finally:
                            try:
                                loop.close()
                            except:
                                pass
                    
                    execution_time = time.time() - start_time
                    if execution_time > 5.0:
                        user_id, _, content = cls._get_event_info(event)
                        _logger.warning(
                            f"插件 [{plugin_name}] 执行耗时 {execution_time:.2f}秒 "
                            f"(用户: {user_id or 'unknown'}, "
                            f"内容: {content[:50]})"
                        )
                    
                    return result
                except Exception as e:
                    error_msg = f"插件 {plugin_name} 执行异常: {str(e)}"
                    error_trace = traceback.format_exc()
                    _log_error(error_msg, error_trace)
                    
                    try:
                        user_id, group_id, _ = cls._get_event_info(event)
                        add_log_to_db(_LOG_TYPE_ERROR, {
                            'timestamp': time.strftime(_TIMESTAMP_FORMAT),
                            'plugin_name': plugin_name,
                            'handler_name': handler_name,
                            'user_id': user_id,
                            'group_id': group_id,
                            'content': error_msg,
                            'traceback': error_trace
                        })
                    except:
                        pass
                    
                    return False
            
            future = _plugin_executor.submit(execute_handler)
            
            try:
                from concurrent.futures import TimeoutError
                return future.result(timeout=_SOFT_TIMEOUT)
            except TimeoutError:
                user_id, _, content = cls._get_event_info(event)
                with _background_tasks_lock:
                    _background_tasks[id(future)] = {
                        'future': future,
                        'start_time': time.time(),
                        'plugin_name': plugin_name,
                        'user_id': user_id,
                        'content': content[:100]
                    }
                return True
                
        finally:
            for method_name, original_method in original_methods.items():
                if original_method:
                    setattr(event, method_name, original_method)
    
    @classmethod
    def _cleanup_background_tasks(cls):
        current_time = time.time()
        with _background_tasks_lock:
            for future_id in list(_background_tasks.keys()):
                task_info = _background_tasks.get(future_id)
                if not task_info:
                    continue
                
                runtime = current_time - task_info['start_time']
                if runtime >= _HARD_TIMEOUT:
                    task_info['future'].cancel()
                    _background_tasks.pop(future_id, None)
                    _log_error(
                        f"插件 {task_info['plugin_name']} 硬超时（{_HARD_TIMEOUT}秒），已强制终止",
                        f"用户: {task_info['user_id']}\n内容: {task_info['content']}"
                    )
                elif task_info['future'].done():
                    _background_tasks.pop(future_id, None)
    
    @classmethod
    def get_background_tasks_status(cls):
        current_time = time.time()
        with _background_tasks_lock:
            return [{
                'plugin_name': task_info['plugin_name'],
                'user_id': task_info['user_id'],
                'content': task_info['content'],
                'runtime': f"{current_time - task_info['start_time']:.1f}秒",
                'is_running': not task_info['future'].done()
            } for task_info in _background_tasks.values()]
    
    @classmethod
    def _create_method_logger(cls, original_methods_dict, plugin_name, is_first_reply, event):
        def _create_logged_method(original_method, method_name):
            def logged_method(*args, **kwargs):
                # 先调用原方法
                result = original_method(*args, **kwargs)
                
                # 提取内容信息
                content_extractors = {
                    'reply': lambda a, k: a[0] if a else k.get('content', ''),
                    'reply_image': lambda a, k: f"[图片] {a[1] if len(a) > 1 else k.get('content', '')}".strip(),
                    'reply_voice': lambda a, k: f"[语音] {a[1] if len(a) > 1 else k.get('content', '')}".strip(),
                    'reply_video': lambda a, k: f"[视频] {a[1] if len(a) > 1 else k.get('content', '')}".strip(),
                    'reply_ark': lambda a, k: f"[ARK] {a[0] if a else k.get('template_id', '')}",
                    'reply_markdown': lambda a, k: f"[MD] {a[0] if a else k.get('template', '')}",
                    'reply_md': lambda a, k: f"[MD] {a[0] if a else k.get('template', '')}",
                    'reply_markdown_aj': lambda a, k: f"[MD_AJ] {a[0] if a else k.get('text', '')}"
                }
                
                extractor = content_extractors.get(method_name)
                text_content = extractor(args, kwargs) if extractor else f"[{method_name}]"
                if method_name == 'reply' and not isinstance(text_content, str):
                    text_content = "[非文本内容]"
                
                user_id = getattr(event, 'user_id', '')
                group_id = getattr(event, 'group_id', None) or _DEFAULT_GROUP_ID
                
                # 获取最后发送的payload
                raw_message = ''
                payload = getattr(event, '_last_sent_payload', None)
                if payload:
                    try:
                        raw_message = json.dumps(payload, ensure_ascii=False)
                    except:
                        raw_message = str(payload)
                    event._last_sent_payload = None
                
                # 记录到web面板
                add_plugin_log(text_content, user_id=user_id, group_id=group_id, plugin_name=plugin_name, raw_message=raw_message)
                
                # 记录到数据库
                from config import SAVE_RAW_MESSAGE_TO_DB
                db_entry = {
                    'timestamp': time.strftime(_TIMESTAMP_FORMAT),
                    'type': 'plugin',
                    'content': text_content,
                    'user_id': user_id,
                    'group_id': group_id,
                    'plugin_name': plugin_name,
                    'raw_message': raw_message if SAVE_RAW_MESSAGE_TO_DB else ''
                }
                add_log_to_db('message', db_entry)
                
                if is_first_reply[0]:
                    is_first_reply[0] = False
                
                return result
            return logged_method
        
        wrapped_methods = {}
        for method_name, original_method in original_methods_dict.items():
            if original_method:
                wrapped_methods[method_name] = _create_logged_method(original_method, method_name)
        
        return wrapped_methods

    # === 默认回复 ===
    @classmethod
    def _get_exclude_patterns(cls):
        if cls._exclude_patterns_cache is None:
            cls._exclude_patterns_cache = []
            for pattern in DEFAULT_RESPONSE_EXCLUDED_REGEX:
                try:
                    cls._exclude_patterns_cache.append(re.compile(pattern))
                except Exception as e:
                    _log_error(f"排除正则 '{pattern}' 编译失败: {str(e)}")
        return cls._exclude_patterns_cache
    
    @classmethod
    def _should_exclude_default_response(cls, content):
        for compiled_pattern in cls._get_exclude_patterns():
            try:
                if compiled_pattern.search(content):
                    return True
            except Exception as e:
                _log_error(f"排除正则匹配错误: {str(e)}")
        return False

    @classmethod
    def send_default_response(cls, event):
        MessageTemplate.send(event, MSG_TYPE_DEFAULT)
    
    @classmethod
    def get_web_routes(cls):
        sorted_routes = sorted(cls._web_routes.items(), key=lambda x: x[1].get('priority', 100))
        return {path: info for path, info in sorted_routes}
    
    @classmethod
    def get_csp_domains(cls):
        """获取所有插件注册的CSP域名配置"""
        return {directive: list(domains) for directive, domains in cls._csp_domains.items()}
    
    @classmethod
    def get_api_routes(cls):
        return cls._api_routes.copy() 