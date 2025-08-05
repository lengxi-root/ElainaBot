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
import asyncio  # 异步IO支持
import json

# ===== 2. 第三方库导入 =====
# （本文件暂未用到第三方库）

# ===== 3. 自定义模块导入 =====
from config import (
    SEND_DEFAULT_RESPONSE, OWNER_IDS, MAINTENANCE_MODE,
    DEFAULT_RESPONSE_EXCLUDED_REGEX
)
from core.plugin.message_templates import MessageTemplate, MSG_TYPE_MAINTENANCE, MSG_TYPE_GROUP_ONLY, MSG_TYPE_OWNER_ONLY, MSG_TYPE_DEFAULT, MSG_TYPE_BLACKLIST
from web.app import add_plugin_log, add_framework_log, add_error_log
from function.log_db import add_log_to_db

# ===== 4. 全局变量与常量 =====
_last_plugin_gc_time = 0  # 上次插件垃圾回收时间
_plugin_gc_interval = 30  # 垃圾回收间隔(秒)
_blacklist_cache = {}     # 黑名单缓存
_blacklist_last_load = 0  # 上次加载黑名单的时间
_blacklist_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "blacklist.json")

# 新增：插件加载优化相关全局变量
_last_quick_check_time = 0  # 上次快速检查时间
_plugins_loaded = False     # 插件是否已加载标记

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
    _file_last_modified = {} # 插件文件修改时间
    _unloaded_modules = []   # 待回收模块
    _regex_cache = {}        # 预编译正则缓存
    _sorted_handlers = []    # 按优先级排序的处理器列表，避免每次重新排序

    # ===== 黑名单相关 =====
    @classmethod
    def load_blacklist(cls):
        """
        加载黑名单数据
        """
        global _blacklist_cache, _blacklist_last_load, _blacklist_file
        
        # 确保data目录存在
        data_dir = os.path.dirname(_blacklist_file)
        if not os.path.exists(data_dir):
            try:
                os.makedirs(data_dir)
                add_framework_log("创建数据目录: data")
            except Exception as e:
                add_error_log(f"创建数据目录失败: {str(e)}")
                
        # 检查黑名单文件是否存在，不存在则创建空文件
        if not os.path.exists(_blacklist_file):
            try:
                with open(_blacklist_file, 'w', encoding='utf-8') as f:
                    json.dump({}, f, ensure_ascii=False, indent=2)
                add_framework_log(f"创建黑名单文件: {_blacklist_file}")
            except Exception as e:
                add_error_log(f"创建黑名单文件失败: {str(e)}")
                return {}
                
        # 检查是否需要重新加载
        current_time = time.time()
        if _blacklist_last_load == 0 or (current_time - _blacklist_last_load > 60):  # 每60秒重新加载一次
            try:
                # 检查文件修改时间
                if os.path.exists(_blacklist_file):
                    mtime = os.path.getmtime(_blacklist_file)
                    # 如果文件已更新或缓存为空，则重新加载
                    if not _blacklist_cache or mtime > _blacklist_last_load:
                        with open(_blacklist_file, 'r', encoding='utf-8') as f:
                            _blacklist_cache = json.load(f)
                            _blacklist_last_load = current_time
                            add_framework_log(f"已加载黑名单数据，共 {len(_blacklist_cache)} 条记录")
            except Exception as e:
                add_error_log(f"加载黑名单数据失败: {str(e)}", traceback.format_exc())
                
        return _blacklist_cache
    
    @classmethod
    def is_blacklisted(cls, user_id):
        """
        检查用户是否在黑名单中
        @return: (是否在黑名单中, 原因)
        """
        if not user_id:
            return False, ""
            
        blacklist = cls.load_blacklist()
        user_id = str(user_id)  # 确保用户ID为字符串
        
        if user_id in blacklist:
            return True, blacklist[user_id]
            
        return False, ""

    # ===== 插件加载相关 =====
    @classmethod
    def load_plugins(cls):
        """
        加载所有插件目录下的插件。
        同时检查已加载插件的更新情况，实现热更新功能。
        新增：插件加载优化，减少不必要的文件系统检查。
        """
        global _last_plugin_gc_time, _last_quick_check_time, _plugins_loaded
        
        current_time = time.time()
        
        # 轻量级快速检查：减少过于频繁的检查，但不影响热加载
        if (_plugins_loaded and 
            current_time - _last_quick_check_time < 0.1):  # 改为0.1秒，避免阻塞热加载
            return len(cls._plugins)
        
        _last_quick_check_time = current_time
        
        script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        plugins_dir = os.path.join(script_dir, 'plugins')
        
        # 确保插件目录存在
        if not os.path.exists(plugins_dir):
            try:
                os.makedirs(plugins_dir)
                add_framework_log("创建插件目录: plugins")
            except Exception as e:
                add_error_log(f"创建插件目录失败: {str(e)}")
                return 0
        
        # 检查已加载插件的文件是否被删除
        deleted_files = []
        for file_path in list(cls._file_last_modified.keys()):
            if not os.path.exists(file_path):
                deleted_files.append(file_path)
        
        # 处理已删除的文件
        for file_path in deleted_files:
            try:
                dir_name = os.path.basename(os.path.dirname(file_path))
                module_name = os.path.splitext(os.path.basename(file_path))[0]
                removed_count = cls._unregister_file_plugins(file_path)
                if file_path in cls._file_last_modified:
                    del cls._file_last_modified[file_path]
                if removed_count > 0:
                    add_framework_log(f"插件更新：检测到文件已删除 {dir_name}/{module_name}.py，已注销 {removed_count} 个处理器")
            except Exception as e:
                add_error_log(f"处理已删除插件文件时出错: {str(e)}", traceback.format_exc())
        
        # 直接扫描并加载所有插件目录
        loaded_count = 0
        for dir_name in os.listdir(plugins_dir):
            dir_path = os.path.join(plugins_dir, dir_name)
            if os.path.isdir(dir_path):
                loaded_count += cls._load_plugins_from_directory(script_dir, dir_name)
        
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
        
        # 标记插件已加载
        _plugins_loaded = True
            
        return loaded_count + main_module_loaded
        
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
                    if module_name.startswith('plugins.'):
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
                    # 自动添加DOTALL标志以支持换行符匹配
                    compiled_regex = re.compile(enhanced_pattern, re.DOTALL)
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
    def _load_plugins_from_directory(cls, script_dir, dir_name):
        """
        加载指定目录下的插件文件。
        """
        plugin_dir = os.path.join(script_dir, 'plugins', dir_name)
        if not os.path.exists(plugin_dir) or not os.path.isdir(plugin_dir):
            # 如果目录不存在，注销该目录下所有已加载的插件
            cls._unregister_directory_plugins(plugin_dir)
            return 0
            
        py_files = [f for f in os.listdir(plugin_dir) if f.endswith('.py') and f != '__init__.py']
        loaded_count = 0
        
        # 记录当前目录下的所有插件文件的绝对路径
        current_files = {os.path.join(plugin_dir, py_file) for py_file in py_files}
        
        # 处理已删除的文件
        dir_files_to_delete = []
        for file_path in list(cls._file_last_modified.keys()):
            # 只处理当前目录下的文件
            if file_path.startswith(plugin_dir):
                # 如果文件不在当前目录中或文件不存在，则需要注销
                if file_path not in current_files or not os.path.exists(file_path):
                    dir_files_to_delete.append(file_path)
        
        # 注销已删除的文件插件
        for file_path in dir_files_to_delete:
            removed_count = cls._unregister_file_plugins(file_path)
            if file_path in cls._file_last_modified:
                del cls._file_last_modified[file_path]
            module_name = os.path.splitext(os.path.basename(file_path))[0]
            add_framework_log(f"插件更新：检测到文件已删除 {dir_name}/{module_name}.py，已注销 {removed_count} 个处理器")
        
        # 加载或更新现有文件
        for py_file in py_files:
            file_path = os.path.join(plugin_dir, py_file)
            if not os.path.exists(file_path):
                continue
                
            try:
                last_modified = os.path.getmtime(file_path)
                if file_path not in cls._file_last_modified or cls._file_last_modified[file_path] < last_modified:
                    cls._file_last_modified[file_path] = last_modified
                    loaded_count += cls._load_plugin_file(file_path, dir_name)
            except (OSError, IOError) as e:
                add_error_log(f"获取文件修改时间失败: {file_path}, 错误: {str(e)}")
                
        return loaded_count
        
    @classmethod
    def _unregister_directory_plugins(cls, plugin_dir):
        """
        注销指定目录下的所有插件。
        """
        removed_count = 0
        dir_files_to_delete = []
        
        # 找出需要删除的文件
        for file_path in list(cls._file_last_modified.keys()):
            if file_path.startswith(plugin_dir):
                dir_files_to_delete.append(file_path)
        
        # 注销每个文件的插件
        for file_path in dir_files_to_delete:
            removed_count += cls._unregister_file_plugins(file_path)
            if file_path in cls._file_last_modified:
                del cls._file_last_modified[file_path]
        
        if removed_count > 0:
            dir_name = os.path.basename(plugin_dir)
            add_framework_log(f"插件更新：检测到目录已删除 {dir_name}，已注销 {removed_count} 个处理器")
            
        return removed_count

    @classmethod
    def _load_plugin_file(cls, plugin_file, dir_name):
        """
        加载单个插件文件。
        """
        plugin_name = os.path.basename(plugin_file)
        module_name = os.path.splitext(plugin_name)[0]
        loaded_count = 0
        
        try:
            # 检查是否为热更新
            module_fullname = f"plugins.{dir_name}.{module_name}"
            is_hot_reload = module_fullname in sys.modules
            
            # 注销旧插件
            cls._unregister_file_plugins(plugin_file)
            
            # 更新文件修改时间
            last_modified = os.path.getmtime(plugin_file)
            cls._file_last_modified[plugin_file] = last_modified
            
            # 保存旧模块引用
            old_module = None
            if module_fullname in sys.modules:
                old_module = sys.modules[module_fullname]
                del sys.modules[module_fullname]
                
            # 加载新模块
            spec = importlib.util.spec_from_file_location(module_fullname, plugin_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_fullname] = module
                
                try:
                    spec.loader.exec_module(module)
                except Exception as e:
                    # 恢复旧模块
                    if old_module:
                        sys.modules[module_fullname] = old_module
                    else:
                        del sys.modules[module_fullname]
                    raise e
                    
                # 将旧模块添加到待回收列表
                if old_module:
                    cls._unloaded_modules.append(old_module)
                    
                # 处理模块中的插件类
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
                    # 热更新成功日志
                    if is_hot_reload:
                        if plugin_load_results:
                            add_framework_log(f"插件热更新成功: {dir_name}/{plugin_name} 已重新加载: {', '.join(plugin_load_results)}")
                        else:
                            add_framework_log(f"插件热更新成功: {dir_name}/{plugin_name} 已重新加载")
                    # 首次加载日志
                    else:
                        if plugin_load_results:
                            add_framework_log(f"插件加载: {dir_name}/{plugin_name} 加载成功: {', '.join(plugin_load_results)}")
                        else:
                            add_framework_log(f"插件加载: {dir_name}/{plugin_name} 加载成功")
                else:
                    add_framework_log(f"插件{'热更新' if is_hot_reload else '加载'}: {dir_name}/{plugin_name} 中未找到有效的插件类")
        except Exception as e:
            error_msg = f"插件{'热更新' if module_fullname in sys.modules else '加载'}: {dir_name}/{plugin_name} 失败: {str(e)}"
            add_error_log(error_msg, traceback.format_exc())
            add_framework_log(error_msg)
            
        return loaded_count

    @classmethod
    def _unregister_file_plugins(cls, plugin_file):
        """
        注销指定文件中的所有插件，返回注销的处理器数量。
        清理插件相关的所有资源，包括线程池、连接池等。
        """
        global _last_plugin_gc_time
        removed = []
        plugin_classes_to_remove = []
        
        # 获取插件所在目录和模块名，用于日志
        if plugin_file:
            module_name = os.path.splitext(os.path.basename(plugin_file))[0]
            dir_name = os.path.basename(os.path.dirname(plugin_file))
            module_fullname = f"plugins.{dir_name}.{module_name}"
            is_hot_reload = module_fullname in sys.modules
            file_exists = os.path.exists(plugin_file)
        else:
            module_name = "未知模块"
            dir_name = "未知目录"
            module_fullname = "unknown"
            is_hot_reload = False
            file_exists = False
        
        # 1. 查找所有需要移除的插件类
        for pattern, handler_info in list(cls._regex_handlers.items()):
            try:
                plugin_class = handler_info.get('class') if isinstance(handler_info, dict) else handler_info[0]
                # 检查插件类是否来自指定文件
                if hasattr(plugin_class, '_source_file') and plugin_class._source_file == plugin_file:
                    removed.append((plugin_class.__name__, pattern))
                    if plugin_class not in plugin_classes_to_remove:
                        plugin_classes_to_remove.append(plugin_class)
            except Exception as e:
                add_error_log(f"查找插件类时出错: {str(e)}", traceback.format_exc())
        
        # 2. 清理正则处理器表中的插件处理器
        for pattern, handler_info in list(cls._regex_handlers.items()):
            try:
                plugin_class = handler_info.get('class') if isinstance(handler_info, dict) else handler_info[0]
                if plugin_class in plugin_classes_to_remove:
                    del cls._regex_handlers[pattern]
                    if pattern in cls._regex_cache:
                        del cls._regex_cache[pattern]
            except Exception as e:
                add_error_log(f"清理正则处理器时出错: {str(e)}", traceback.format_exc())
        
        # 3. 清理插件类实例和资源
        for plugin_class in plugin_classes_to_remove:
            try:
                # 移除插件类
                if plugin_class in cls._plugins:
                    del cls._plugins[plugin_class]
                
                # 尝试调用插件的清理方法（如果存在）
                if hasattr(plugin_class, 'cleanup') and callable(getattr(plugin_class, 'cleanup')):
                    try:
                        plugin_class.cleanup()
                    except Exception as e:
                        add_error_log(f"插件 {plugin_class.__name__} 清理资源失败: {str(e)}", traceback.format_exc())
                
                # 查找并清理插件类的线程池
                for attr_name in dir(plugin_class):
                    if attr_name.startswith('__'):
                        continue
                        
                    try:
                        attr = getattr(plugin_class, attr_name)
                        # 清理线程池
                        if attr_name.endswith('_pool') or attr_name.endswith('_thread_pool'):
                            if hasattr(attr, 'shutdown') and callable(getattr(attr, 'shutdown')):
                                try:
                                    attr.shutdown(wait=False)
                                    add_framework_log(f"插件热更新：关闭 {plugin_class.__name__} 的线程池 {attr_name}")
                                except Exception:
                                    pass
                        # 清理连接池
                        elif attr_name.endswith('_conn_pool') or attr_name.endswith('_connection_pool'):
                            if hasattr(attr, 'close') and callable(getattr(attr, 'close')):
                                try:
                                    attr.close()
                                    add_framework_log(f"插件热更新：关闭 {plugin_class.__name__} 的连接池 {attr_name}")
                                except Exception:
                                    pass
                        # 清理事件循环
                        elif attr_name.endswith('_loop') and hasattr(attr, 'is_running') and hasattr(attr, 'stop'):
                            if attr.is_running():
                                try:
                                    attr.stop()
                                    add_framework_log(f"插件热更新：停止 {plugin_class.__name__} 的事件循环 {attr_name}")
                                except Exception:
                                    pass
                    except Exception:
                        pass
            except Exception as e:
                add_error_log(f"清理插件类资源时出错: {str(e)}", traceback.format_exc())
        
        # 4. 清理模块
        if module_fullname != "unknown" and module_fullname in sys.modules:
            try:
                module = sys.modules[module_fullname]
                
                # 尝试调用模块级别的清理函数
                if hasattr(module, 'cleanup') and callable(module.cleanup):
                    try:
                        module.cleanup()
                    except Exception as e:
                        add_error_log(f"模块 {module_fullname} 清理函数执行失败: {str(e)}")
                
                # 检查模块中的全局线程池或连接池
                for attr_name in dir(module):
                    if attr_name.startswith('__'):
                        continue
                        
                    try:
                        attr = getattr(module, attr_name)
                        # 清理线程池
                        if (attr_name.endswith('_pool') or attr_name.endswith('_thread_pool')) and not isinstance(attr, type):
                            if hasattr(attr, 'shutdown') and callable(getattr(attr, 'shutdown')):
                                try:
                                    attr.shutdown(wait=False)
                                except Exception:
                                    pass
                        # 清理连接池
                        elif (attr_name.endswith('_conn_pool') or attr_name.endswith('_connection_pool')) and not isinstance(attr, type):
                            if hasattr(attr, 'close') and callable(getattr(attr, 'close')):
                                try:
                                    attr.close()
                                except Exception:
                                    pass
                    except Exception:
                        pass
                
                # 将模块添加到待回收列表
                cls._unloaded_modules.append(module)
                
                # 从sys.modules中删除模块
                if module_fullname in sys.modules:
                    del sys.modules[module_fullname]
                
                # 记录热更新日志
                if is_hot_reload:
                    status = "已删除" if not file_exists else "变更"
                    add_framework_log(f"插件热更新：检测到文件{status} {dir_name}/{module_name}.py，已注销 {len(removed)} 个处理器")
            except Exception as e:
                add_error_log(f"清理模块时出错: {str(e)}", traceback.format_exc())
        
        # 5. 执行垃圾回收
        current_time = time.time()
        if removed and (current_time - _last_plugin_gc_time >= _plugin_gc_interval):
            gc.collect()
            _last_plugin_gc_time = current_time
            
        return len(removed)

    # ===== 正则处理器优化 =====
    @classmethod 
    def _rebuild_sorted_handlers(cls):
        """重建排序的处理器列表，避免每次消息处理都重新排序"""
        handlers_with_priority = []
        
        for pattern, handler_info in cls._regex_handlers.items():
            plugin_class = handler_info.get('class')
            priority = cls._plugins.get(plugin_class, 10)  # 默认优先级10
            
            handlers_with_priority.append({
                'pattern': pattern,
                'handler_info': handler_info,
                'priority': priority
            })
        
        # 按优先级排序（数字越小，优先级越高）
        cls._sorted_handlers = sorted(handlers_with_priority, key=lambda x: x['priority'])

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
                # 自动添加DOTALL标志以支持换行符匹配
                compiled_regex = re.compile(enhanced_pattern, re.DOTALL)
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
        
        # 重建排序的处理器列表
        cls._rebuild_sorted_handlers()
        
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
            # 检查插件更新
            cls.load_plugins()
            
            # 0. 检查消息是否已被处理（如在MessageEvent中直接处理的消息）
            if hasattr(event, 'handled') and event.handled:
                return True
            
            # 0.1 检查用户是否在黑名单中
            if hasattr(event, 'user_id') and event.user_id:
                is_blacklisted, reason = cls.is_blacklisted(event.user_id)
                if is_blacklisted:
                    add_plugin_log(f"用户 {event.user_id} 在黑名单中，拒绝处理消息，原因: {reason}")
                    MessageTemplate.send(event, MSG_TYPE_BLACKLIST, reason=reason)
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
            else:
                add_plugin_log("未匹配到任何插件，且未启用默认回复")
            
            # 记录未匹配的消息到数据库
            cls._log_unmatched_message(event, original_content)
                    
        return matched

    @classmethod
    def _find_matched_handlers(cls, event_content, event, is_owner, is_group, permission_denied=None):
        """
        查找匹配内容的所有处理器，使用预排序优化性能。
        同时检查权限条件，过滤掉不符合条件的处理器
        
        @param event_content: 消息内容
        @param event: 消息事件对象
        @param is_owner: 是否为主人
        @param is_group: 是否在群聊中
        @param permission_denied: 权限拒绝信息记录
        @return: 满足所有条件的处理器列表（已按优先级排序）
        """
        matched_handlers = []
        
        # 使用预排序的处理器列表，避免每次重新排序
        for handler_data in cls._sorted_handlers:
            pattern = handler_data['pattern'] 
            handler_info = handler_data['handler_info']
            
            # 1. 编译并匹配正则
            compiled_regex = cls._regex_cache.get(pattern)
            if not compiled_regex:
                try:
                    # 自动添加DOTALL标志以支持换行符匹配
                    compiled_regex = re.compile(pattern, re.DOTALL)
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
                
            # 4. 添加通过所有检查的处理器（无需再排序，已预排序）
            matched_handlers.append({
                'pattern': pattern,
                'match': match,
                'plugin_class': plugin_class,
                'handler_name': handler_name,
                'priority': handler_data['priority']
            })
            
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
                
                # 记录到数据库日志
                try:
                    add_log_to_db('error', {
                        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                        'content': error_msg,
                        'traceback': stack_trace
                    })
                except Exception:
                    pass
                    
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
        支持同步和异步处理函数。
        """
        # 保存原始发送方法
        original_reply = event.reply
        original_reply_image = getattr(event, 'reply_image', None)
        original_reply_voice = getattr(event, 'reply_voice', None) 
        original_reply_video = getattr(event, 'reply_video', None)
        original_reply_ark = getattr(event, 'reply_ark', None)
        original_reply_markdown = getattr(event, 'reply_markdown', None)
        original_reply_md = getattr(event, 'reply_md', None)
        
        is_first_reply = [True]
        
        def _create_logged_method(original_method, method_name):
            """创建带日志记录的方法"""
            def logged_method(*args, **kwargs):
                # 提取内容信息
                if method_name == 'reply':
                    content = args[0] if args else kwargs.get('content', '')
                    text_content = content if isinstance(content, str) else "[非文本内容]"
                elif method_name == 'reply_image':
                    content = args[1] if len(args) > 1 else kwargs.get('content', '')
                    text_content = f"[图片消息] {content}" if content else "[图片消息]"
                elif method_name == 'reply_voice':
                    content = args[1] if len(args) > 1 else kwargs.get('content', '')
                    text_content = f"[语音消息] {content}" if content else "[语音消息]"
                elif method_name == 'reply_video':
                    content = args[1] if len(args) > 1 else kwargs.get('content', '')
                    text_content = f"[视频消息] {content}" if content else "[视频消息]"
                elif method_name == 'reply_ark':
                    template_id = args[0] if args else kwargs.get('template_id', '')
                    content = args[2] if len(args) > 2 else kwargs.get('content', '')
                    text_content = f"[ARK卡片] 模板ID:{template_id} {content}".strip()
                elif method_name in ('reply_markdown', 'reply_md'):
                    template = args[0] if args else kwargs.get('template', '')
                    text_content = f"[Markdown模板] 模板:{template}"
                else:
                    text_content = f"[{method_name}调用]"
                
                # 记录日志
                if is_first_reply[0]:
                    add_plugin_log(f"插件 {plugin_name} 回复：{text_content} (处理完成)")
                    is_first_reply[0] = False
                else:
                    add_plugin_log(f"插件 {plugin_name} 回复：{text_content} (继续处理)")
                
                # 调用原始方法
                return original_method(*args, **kwargs)
            return logged_method
        
        # 替换所有发送方法
        event.reply = _create_logged_method(original_reply, 'reply')
        
        if original_reply_image:
            event.reply_image = _create_logged_method(original_reply_image, 'reply_image')
        if original_reply_voice:
            event.reply_voice = _create_logged_method(original_reply_voice, 'reply_voice')
        if original_reply_video:
            event.reply_video = _create_logged_method(original_reply_video, 'reply_video')
        if original_reply_ark:
            event.reply_ark = _create_logged_method(original_reply_ark, 'reply_ark')
        if original_reply_markdown:
            event.reply_markdown = _create_logged_method(original_reply_markdown, 'reply_markdown')
        if original_reply_md:
            event.reply_md = _create_logged_method(original_reply_md, 'reply_md')
        
        try:
            handler = getattr(plugin_class, handler_name)
            result = handler(event)
            
            # 检查结果是否为协程对象
            if asyncio.iscoroutine(result):
                # 如果是协程，使用asyncio运行它
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    # 如果没有事件循环，创建一个新的
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                # 运行协程直到完成
                result = loop.run_until_complete(result)
                
            return result
        finally:
            # 恢复原始方法
            event.reply = original_reply
            if original_reply_image:
                event.reply_image = original_reply_image
            if original_reply_voice:
                event.reply_voice = original_reply_voice
            if original_reply_video:
                event.reply_video = original_reply_video
            if original_reply_ark:
                event.reply_ark = original_reply_ark
            if original_reply_markdown:
                event.reply_markdown = original_reply_markdown
            if original_reply_md:
                event.reply_md = original_reply_md

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
    
    @classmethod
    def _log_unmatched_message(cls, event, original_content):
        """
        记录未匹配任何插件的消息到数据库
        @param event: 消息事件对象
        @param original_content: 原始消息内容
        """
        try:
            import datetime
            
            # 获取群聊ID，私聊默认为c2c
            group_id = 'c2c'
            if hasattr(event, 'group_id') and event.group_id:
                group_id = str(event.group_id)
            elif cls._is_group_chat(event):
                # 从事件中尝试获取群聊ID
                if hasattr(event, 'get'):
                    group_id = event.get('d/group_openid') or event.get('d/guild_id') or 'unknown_group'
                else:
                    group_id = 'unknown_group'
            
            # 获取用户ID
            user_id = str(event.user_id) if hasattr(event, 'user_id') and event.user_id else '未知用户'
            
            # 获取消息内容
            content = original_content if original_content else (event.content if hasattr(event, 'content') else '')
            
            # 构建原始消息数据（JSON格式）
            raw_message_data = {}
            if hasattr(event, '__dict__'):
                # 提取事件对象的关键属性
                for attr in ['message_id', 'user_id', 'group_id', 'content', 'message_type', 'event_type']:
                    if hasattr(event, attr):
                        raw_message_data[attr] = getattr(event, attr)
                
                # 如果事件有原始数据，也添加进去（限制大小避免过大）
                if hasattr(event, 'get') and callable(event.get):
                    try:
                        raw_data = {
                            'd': event.get('d') if event.get('d') else {},
                            'op': event.get('op'),
                            's': event.get('s'),
                            't': event.get('t')
                        }
                        raw_message_data['raw'] = raw_data
                    except Exception:
                        pass
            
            # 转换为JSON字符串，限制长度避免数据库字段溢出
            raw_message_json = json.dumps(raw_message_data, ensure_ascii=False, default=str)
            if len(raw_message_json) > 10000:  # 限制为10KB
                raw_message_json = raw_message_json[:10000] + "...(truncated)"
            
            # 构建日志数据
            log_data = {
                'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'user_id': user_id,
                'group_id': group_id,
                'content': content[:5000] if content else '',  # 限制内容长度
                'raw_message': raw_message_json
            }
            
            # 写入数据库
            success = add_log_to_db('unmatched', log_data)

        except Exception as e:
            add_error_log(f"记录未匹配消息时出错: {str(e)}", traceback.format_exc()) 