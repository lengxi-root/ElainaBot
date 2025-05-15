import os
import re
import importlib.util
from abc import ABC, abstractmethod

class Plugin(ABC):
    """插件基类"""
    @staticmethod
    @abstractmethod
    def get_regex_handlers():
        """
        注册正则规则与处理函数
        :return: dict[str, callable] - {正则表达式: 处理函数}
        """
        pass

class PluginManager:
    _regex_handlers = {}

    @classmethod
    def load_plugins(cls):
        """加载所有插件"""
        plugin_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'plugins')
        
        # 确保plugins目录存在
        if not os.path.exists(plugin_dir):
            os.makedirs(plugin_dir)
            
        # 遍历所有插件目录
        for plugin_name in os.listdir(plugin_dir):
            plugin_path = os.path.join(plugin_dir, plugin_name, 'main.py')
            if os.path.isfile(plugin_path):
                try:
                    # 动态导入插件模块
                    spec = importlib.util.spec_from_file_location(f"{plugin_name}_plugin", plugin_path)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    
                    # 获取插件类（约定类名为 PluginName_Plugin）
                    plugin_class = getattr(module, f"{plugin_name}_Plugin", None)
                    if plugin_class and issubclass(plugin_class, Plugin):
                        cls.register_plugin(plugin_class)
                except Exception as e:
                    print(f"加载插件 {plugin_name} 失败: {str(e)}")

    @classmethod
    def register_plugin(cls, plugin_class):
        """注册单个插件"""
        for pattern, handler in plugin_class.get_regex_handlers().items():
            cls._regex_handlers[pattern] = (plugin_class, handler)

    @classmethod
    def dispatch_message(cls, event):
        """匹配消息并触发处理函数"""
        for pattern, (plugin_class, handler) in cls._regex_handlers.items():
            matches = re.match(pattern, event.content)
            if matches:
                event.matches = matches.groups()
                handler_method = getattr(plugin_class, handler)
                handler_method(event)
                return True  # 匹配成功则终止后续匹配
        return False  # 无匹配规则 