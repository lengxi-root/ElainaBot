from core.plugin.plugin_manager import Plugin

class example_Plugin(Plugin):
    @staticmethod
    def get_regex_handlers():
        return {
            r'^你好$': 'handle_hello',
            r'^echo\s+(.+)$': 'handle_echo'
        }
        
    @staticmethod
    def handle_hello(event):
        event.reply("你好！我是机器人！")
        
    @staticmethod
    def handle_echo(event):
        if event.matches and len(event.matches) > 0:
            event.reply(event.matches[0]) 