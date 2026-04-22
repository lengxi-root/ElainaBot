#!/usr/bin/env python
# -*- coding: utf-8 -*-

#消息模板系统
import random
from config import USE_MARKDOWN
from function.Access import BOTAPI, Json
# 消息类型常量
MSG_TYPE_WELCOME = 'welcome'                # 群欢迎消息
MSG_TYPE_USER_WELCOME = 'user_welcome'      # 新用户欢迎消息
MSG_TYPE_FRIEND_ADD = 'friend_add'          # 添加好友欢迎消息
MSG_TYPE_GROUP_ONLY = 'group_only'          # 群聊专用命令提示
MSG_TYPE_DEFAULT = 'default'                # 默认回复
MSG_TYPE_OWNER_ONLY = 'owner_only'          # 主人专属命令提示
MSG_TYPE_MAINTENANCE = 'maintenance'        # 维护模式回复
MSG_TYPE_API_ERROR = 'api_error'            # API错误提示消息
MSG_TYPE_BLACKLIST = 'blacklist'            # 黑名单用户提示消息
MSG_TYPE_GROUP_BLACKLIST = 'group_blacklist'  # 群黑名单提示消息

# 消息类型映射常量
GROUP_MESSAGE = 'GROUP_AT_MESSAGE_CREATE'
DIRECT_MESSAGE = 'C2C_MESSAGE_CREATE'
INTERACTION = 'INTERACTION_CREATE'
CHANNEL_MESSAGE = 'AT_MESSAGE_CREATE'
GROUP_ADD_ROBOT = 'GROUP_ADD_ROBOT'

def _handle_welcome(event, **kwargs):
    """群欢迎消息处理"""
    # 按钮示例（已注释，需要时取消注释并传入 event.reply 第二个参数）：
    # btn = event.button([
    #     event.rows([
    #         {'text': '娱乐菜单', 'data': '/娱乐菜单', 'type': 2, 'style': 1, 'enter': True},
    #         {'text': '邀我进群', 'data': 'https://qun.qq.com/qunpro/robot/qunshare?robot_appid=102134274&robot_uin=3889045760', 'type': 0, 'style': 1}
    #     ])
    # ])
    result = event.reply("感谢邀请我进群，发送菜单查看全部帮助。")
    return result is not None

def _handle_user_welcome(event, **kwargs):
    """新用户欢迎消息处理"""
    user_id = getattr(event, 'user_id', None)
    user_count = kwargs.get('user_count', 1)
    welcome_msg = (
        f"![伊蕾娜 #200px #200px](https://q.qlogo.cn/qqapp/102134274/{user_id}/640)\n"
        f"欢迎<@{user_id}>！您是第{user_count}位使用伊蕾娜的伊宝！  \n"
        f"\n> 可以把伊蕾娜邀请到任意群使用哦！"
    ) if USE_MARKDOWN else (
        f"欢迎<@{user_id}>！您是第{user_count}位使用伊蕾娜的伊宝！\n\n可以把伊蕾娜邀请到任意群使用哦！"
    )
    result = event.reply(welcome_msg)
    return result is not None

def _handle_friend_add(event, **kwargs):
    """好友添加欢迎消息处理"""
    user_id = getattr(event, 'user_id', None)
    welcome_msg = (
        f"![伊蕾娜 #360px #360px](https://q.qlogo.cn/qqapp/102134274/{user_id}/640)\n"
        f"欢迎<@{user_id}>！感谢您添加我为好友！  \n"
        f"\n> 您可以直接在这里与我对话，也可以邀请我到您的群聊中使用更多功能！\n"
        f"> 私聊支持的功能相对较少，更多精彩功能请在群聊中体验~"
    ) if USE_MARKDOWN else (
        f"欢迎<@{user_id}>！感谢您添加我为好友！\n\n"
        f"您可以直接在这里与我对话，也可以邀请我到您的群聊中使用更多功能！\n"
        f"私聊支持的功能相对较少，更多精彩功能请在群聊中体验~"
    )
    result = event.reply(welcome_msg)
    return result is not None

def _handle_group_only(event, **kwargs):
    """群聊专用命令提示处理"""
    user_id = getattr(event, 'user_id', None)
    result = event.reply(f"<@{user_id}> 该指令仅在群聊中可用，请在群聊中使用")
    return result is not None

def _handle_default(event, **kwargs):
    """默认回复处理"""
    user_id = getattr(event, 'user_id', None)
    result = event.reply(f"<@{user_id}> 指令错误或不存在，请检查您的输入")
    return result is not None

def _handle_owner_only(event, **kwargs):
    """主人专属命令提示处理"""
    user_id = getattr(event, 'user_id', None)
    result = event.reply(f"<@{user_id}> 暂无权限，你无法操作此命令")
    return result is not None

def _handle_maintenance(event, **kwargs):
    """维护模式回复处理"""
    result = event.reply("系统正在维护中，请稍后再试...\n>当前功能暂时不可用，维护完成后将自动恢复")
    return result is not None

def _handle_api_error(event, **kwargs):
    """API错误消息处理"""
    error_code = kwargs.get('error_code')
    error_message = kwargs.get('error_message', '')
    endpoint = kwargs.get('endpoint')

    user_tip = f"\n消息发送失败\n\n>code:{error_code}\n{error_message}"

    try:
        error_payload = {
            "msg_type": 2,
            "msg_seq": random.randint(10000, 999999),
            "markdown": {"content": user_tip}
        }
        if event.message_type in (GROUP_MESSAGE, DIRECT_MESSAGE):
            error_payload["msg_id"] = event.message_id
        elif event.message_type in (INTERACTION, GROUP_ADD_ROBOT):
            error_payload["event_id"] = event.get('id') or ""
        elif event.message_type == CHANNEL_MESSAGE:
            error_payload["msg_id"] = event.get('id')
        BOTAPI(endpoint, "POST", Json(error_payload))
    except Exception:
        pass

    return user_tip

def _handle_blacklist(event, **kwargs):
    """黑名单用户消息处理"""
    user_id = getattr(event, 'user_id', None)
    reason = kwargs.get('reason', '未指明原因')
    msg = f"<@{user_id}> 您已被列入黑名单，无法使用任何指令，如有误判，请联系管理员\n\n>原因：{reason}"
    result = event.reply(msg)
    return result is not None

def _handle_group_blacklist(event, **kwargs):
    """群黑名单消息处理"""
    result = event.reply("该群组已被列入黑名单，机器人已停止服务\n\n>如有疑问，请联系管理员")
    return result is not None

# 消息处理器映射表
MESSAGE_HANDLERS = {
    MSG_TYPE_WELCOME: _handle_welcome,
    MSG_TYPE_USER_WELCOME: _handle_user_welcome,
    MSG_TYPE_FRIEND_ADD: _handle_friend_add,
    MSG_TYPE_GROUP_ONLY: _handle_group_only,
    MSG_TYPE_DEFAULT: _handle_default,
    MSG_TYPE_OWNER_ONLY: _handle_owner_only,
    MSG_TYPE_MAINTENANCE: _handle_maintenance,
    MSG_TYPE_API_ERROR: _handle_api_error,
    MSG_TYPE_BLACKLIST: _handle_blacklist,
    MSG_TYPE_GROUP_BLACKLIST: _handle_group_blacklist,
}

class MessageTemplate:
    """统一的消息模板系统"""

    @staticmethod
    def send(event, msg_type, **kwargs):
        handler = MESSAGE_HANDLERS.get(msg_type)
        if not handler:
            return False
        try:
            return handler(event, **kwargs)
        except Exception:
            return False

    @staticmethod
    def register_handler(msg_type, handler_func):
        MESSAGE_HANDLERS[msg_type] = handler_func