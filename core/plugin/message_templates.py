#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
消息模板系统
使用映射方式处理各种消息模板，便于快速查找和单独修改
"""

import json
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
GROUP_MESSAGE = 'GROUP_AT_MESSAGE_CREATE'      # 群消息类型
DIRECT_MESSAGE = 'C2C_MESSAGE_CREATE'          # 私聊消息类型
INTERACTION = 'INTERACTION_CREATE'             # 按钮交互消息类型
CHANNEL_MESSAGE = 'AT_MESSAGE_CREATE'          # 频道消息类型
GROUP_ADD_ROBOT = 'GROUP_ADD_ROBOT'            # 被拉进群事件类型

def _handle_welcome(event, **kwargs):
    """群欢迎消息处理"""
    if USE_MARKDOWN:
        # Markdown模式：包含图片和按钮
        message = "![感谢 #1755px #2048px](https://lengxi-1323728141.cos.ap-guangzhou.myqcloud.com/%E5%9B%BA%E5%AE%9A%E5%9B%BE%E7%89%87/%E7%BE%A4%E6%AC%A2%E8%BF%8E.jpg)\n__「你绝不是只身一人」 「我一直在你身边。」\n今朝依旧，今后亦然。__\n\n>大家好，我是有着沉鱼落雁般美貌的灰之魔女伊蕾娜！\n\n>可以为群内提供各种各样的群娱互动，与一些高质量图库功能，欢迎大家使用！\n***\n\n>注:所有指令必须_[@伊蕾娜]_才能使用,可以先尝试发送娱乐菜单，有按钮可以一键发送命令使用哦~\n"
        
        btn = event.button([
            event.rows([{
                'text': '娱乐菜单',
                'data': '/娱乐菜单',
                'type': 2,
                'style': 1,
                'enter': True,
            },{
                'text': '今日老婆',
                'data': '/今日老婆',
                'type': 2,
                'style': 1,
                'enter': True,
            }]),
            event.rows([{
                'text': '关于',
                'data': '/关于',
                'type': 2,
                'style': 1,
                'enter': True,
            },{
                'text': '邀我进群',
                'data': 'https://qun.qq.com/qunpro/robot/qunshare?robot_appid=102134274&robot_uin=3889045760',
                'type': 0,
                'style': 1
            }])
        ])
        result = event.reply(message, btn)
    else:
        # 非Markdown模式：纯文本消息，无图片和按钮
        message = "「你绝不是只身一人」 「我一直在你身边。」\n今朝依旧，今后亦然。\n\n大家好，我是有着沉鱼落雁般美貌的灰之魔女伊蕾娜！\n\n可以为群内提供各种各样的群娱互动，与一些高质量图库功能，欢迎大家使用！\n\n注:所有指令必须@伊蕾娜才能使用,可以先尝试发送娱乐菜单！"
        result = event.reply(message)
    
    return result is not None

def _handle_user_welcome(event, **kwargs):
    """新用户欢迎消息处理"""
    user_id = event.user_id if hasattr(event, 'user_id') else None
    user_count = kwargs.get('user_count', 1)
    
    if USE_MARKDOWN:
        # Markdown模式：包含QQ头像图片和按钮
        welcome_msg = (
            f"![伊蕾娜 #200px #200px](https://q.qlogo.cn/qqapp/102134274/{user_id}/640)\n"
            f"欢迎<@{user_id}>！您是第{user_count}位使用伊蕾娜的伊宝！  \n"
            f"\n> 可以把伊蕾娜邀请到任意群使用哦！"
        )
        
        btn = event.button([
            event.rows([
                {
                    'text': '🎄️ 菜单',
                    'data': '菜单',
                    'enter': True,
                    'style': 1
                },
                {
                    'text': '🪀️ 娱乐菜单',
                    'data': '/娱乐菜单',
                    'enter': True,
                    'style': 1
                }
            ]),
            event.rows([
                {
                    'text': '♥️ 群友老婆',
                    'data': '/群友老婆',
                    'enter': True,
                    'style': 1
                },
                {
                    'text': '✨ 今日老婆',
                    'data': '/今日老婆',
                    'enter': True,
                    'style': 1
                }
            ]),
            event.rows([
                {
                    'text': '🎆 邀伊蕾娜进群',
                    'data': 'https://qun.qq.com/qunpro/robot/qunshare?robot_appid=102134274&robot_uin=3889045760',
                    'type': 0,
                    'style': 1
                }
            ])
        ])
        result = event.reply(welcome_msg, btn)
    else:
        # 非Markdown模式：纯文本消息，无QQ头像图片和按钮
        welcome_msg = f"欢迎<@{user_id}>！您是第{user_count}位使用伊蕾娜的伊宝！\n\n可以把伊蕾娜邀请到任意群使用哦！"
        result = event.reply(welcome_msg)
    
    return result is not None

def _handle_friend_add(event, **kwargs):
    """好友添加欢迎消息处理"""
    user_id = event.user_id if hasattr(event, 'user_id') else None
    
    if USE_MARKDOWN:
        # Markdown模式：包含QQ头像图片和按钮
        welcome_msg = (
            f"![伊蕾娜 #360px #360px](https://q.qlogo.cn/qqapp/102134274/{user_id}/640)\n"
            f"欢迎<@{user_id}>！感谢您添加伊蕾娜为好友！  \n"
            f"\n> 您可以直接在这里与我对话，也可以邀请我到您的群聊中使用更多功能！\n"
            f"> 私聊支持的功能相对较少，更多精彩功能请在群聊中体验~"
        )
        
        btn = event.button([
            event.rows([
                {
                    'text': '📋 菜单',
                    'data': '菜单',
                    'enter': True,
                    'style': 1
                },
                {
                    'text': '🎮 娱乐菜单',
                    'data': '/娱乐菜单',
                    'enter': True,
                    'style': 1
                }
            ]),
            event.rows([
                {
                    'text': '💖 今日老婆',
                    'data': '/今日老婆',
                    'enter': True,
                    'style': 1
                },
                {
                    'text': '🎲 今日运势',
                    'data': '/今日运势',
                    'enter': True,
                    'style': 1
                }
            ]),
            event.rows([
                {
                    'text': '🎆 邀请我进群',
                    'data': 'https://qun.qq.com/qunpro/robot/qunshare?robot_appid=102134274&robot_uin=3889045760',
                    'type': 0,
                    'style': 1
                }
            ])
        ])
        result = event.reply(welcome_msg, btn)
    else:
        # 非Markdown模式：纯文本消息，无QQ头像图片和按钮
        welcome_msg = (
            f"欢迎<@{user_id}>！感谢您添加伊蕾娜为好友！\n\n"
            f"您可以直接在这里与我对话，也可以邀请我到您的群聊中使用更多功能！\n"
            f"私聊支持的功能相对较少，更多精彩功能请在群聊中体验~"
        )
        result = event.reply(welcome_msg)
    
    return result is not None

def _handle_group_only(event, **kwargs):
    """群聊专用命令提示处理"""
    user_id = event.user_id if hasattr(event, 'user_id') else None
    
    if USE_MARKDOWN:
        # Markdown模式：包含按钮
        btn = event.button([
            event.rows([{
                'text': '提示',
                'data': '仅限群聊',
                'type': 2,
                'list': [],  # 空数组，任何人都不能点击
                'style': 0,  # 红色警告风格
                'enter': False
            },{
                'text': '邀请我进群',
                'data': 'https://qun.qq.com/qunpro/robot/qunshare?robot_appid=102134274&robot_uin=3889045760',
                'type': 0,  # 链接类型
                'style': 1
            }])
        ])
        result = event.reply(f"<@{user_id}> 该指令仅在群聊中可用，请在群聊中使用", btn)
    else:
        # 非Markdown模式：纯文本消息，无按钮
        result = event.reply(f"<@{user_id}> 该指令仅在群聊中可用，请在群聊中使用")
    
    return result is not None

def _handle_default(event, **kwargs):
    """默认回复处理"""
    user_id = event.user_id if hasattr(event, 'user_id') else None
    
    if USE_MARKDOWN:
        # Markdown模式：包含图片和按钮
        btn = event.button([
            event.rows([
               {
                'text': '菜单',
                'data': '/菜单',
                'enter': True,
                'style': 1
            }, {
                'text': '娱乐菜单',
                'data': '/娱乐菜单',
                'enter': True,
                'style': 1
            }
            ]),
            event.rows([
               {
                'text': '盯伊蕾娜',
                'data': '/盯伊蕾娜',
                'enter': True,
                'style': 1
            }, {
                'text': '邀我进群',
                'data': 'https://qun.qq.com/qunpro/robot/qunshare?robot_appid=102134274&robot_uin=3889045760',
                'type': 0,
                'style': 1
            }
            ]),
            event.rows([
               {
                'text': '反馈与投稿',
                'data': 'https://www.wjx.cn/vm/wVmvlOu.aspx',
                'type': 0,
                'style': 4
            }, {
                'text': '提供赞助',
                'data': 'https://afdian.com/a/VSTlengxi',
                'type': 0,
                'style': 4
            }
            ])
        ])
        result = event.reply(f"![错误指令 #1360px #680px](https://lengxi-1323728141.cos.ap-guangzhou.myqcloud.com/%E5%9B%BA%E5%AE%9A%E5%9B%BE%E7%89%87/%E9%94%99%E8%AF%AF%E6%8C%87%E4%BB%A4.png)\n<@{user_id}> ", btn)
    else:
        # 非Markdown模式：纯文本消息，无图片和按钮
        result = event.reply(f"<@{user_id}> 指令错误或不存在，请检查您的输入")
    
    return result is not None

def _handle_owner_only(event, **kwargs):
    """主人专属命令提示处理"""
    user_id = event.user_id if hasattr(event, 'user_id') else None
    result = event.reply(f"<@{user_id}> 暂无权限，你无法操作此命令")
    return result is not None

def _handle_maintenance(event, **kwargs):
    """维护模式回复处理"""
    message = "系统正在维护中，请稍后再试...\n>当前功能暂时不可用，维护完成后将自动恢复"
    
    if USE_MARKDOWN:
        # Markdown模式：包含按钮
        buttons = event.button([
            event.rows([
                {
                    'text': '联系管理员',
                    'data': '联系管理员',
                    'enter': True,
                    'style': 5
                }
            ])
        ])
        result = event.reply(message, buttons)
    else:
        # 非Markdown模式：纯文本消息，无按钮
        result = event.reply(message)
    
    return result is not None

def _handle_api_error(event, **kwargs):
    """API错误消息处理"""
    error_code = kwargs.get('error_code')
    error_message = kwargs.get('error_message', '')
    trace_id = kwargs.get('trace_id')
    endpoint = kwargs.get('endpoint')
    
    # 根据不同错误码定制用户友好的错误提示（QQ用户看到的）
    if error_code == 40034006:
        user_tip = f"\n消息发送失败\n\n>消息违规\ncode:{error_code}\n注：请截图选一种方式反馈"
        show_feedback_buttons = True
    elif error_code == 40054017:
        user_tip = f"\n消息发送失败\n\n>消息被拦截\ncode:{error_code}\n注：可能因你的群昵称导致，请更换群昵称尝试"
        show_feedback_buttons = True
    elif error_code == 50015006:
        user_tip = f"\n消息发送失败\n\n>系统繁忙\ncode:{error_code}\n注：稍后重试"
        show_feedback_buttons = False
    elif error_code == 40054010 or error_code == 40034028:
        user_tip = f"\n消息发送失败\n\n>禁止发送url\ncode:{error_code}\n注：请截图选一种方式反馈"
        show_feedback_buttons = True
    else:
        user_tip = f"\n消息发送失败\n\n>未知错误\ncode:{error_code}\n注：出现错误，请截图进行反馈"
        show_feedback_buttons = True
    
    try:
        error_payload = {
            "msg_type": 2,
            "msg_seq": random.randint(10000, 999999),
            "markdown": {
                "content": user_tip
            }
        }
        
        # 只在Markdown模式且需要时添加反馈按钮
        if USE_MARKDOWN and show_feedback_buttons:
            feedback_button = event.button([
                event.rows([
                    {
                        'text': '加群',
                        'link': 'https://qm.qq.com/q/w5kFw95zDq',
                        'type': 0,
                        'style': 1
                    },
                    {
                        'text': '频道',
                        'link': 'https://pd.qq.com/s/4mrty8rgq?b=9',
                        'type': 0,
                        'style': 1
                    },
                    {
                        'text': '问卷',
                        'link': 'https://www.wjx.cn/vm/wVmvlOu.aspx',
                        'type': 0,
                        'style': 1
                    },
                ])
            ])
            error_payload["keyboard"] = feedback_button
        
        # 根据消息类型设置相应的消息ID或事件ID
        if event.message_type == GROUP_MESSAGE or event.message_type == DIRECT_MESSAGE:
            error_payload["msg_id"] = event.message_id
        elif event.message_type == INTERACTION or event.message_type == GROUP_ADD_ROBOT:
            error_payload["event_id"] = event.get('id') or ""
        elif event.message_type == CHANNEL_MESSAGE:
            error_payload["msg_id"] = event.get('id')
            
        # 发送API错误提示
        BOTAPI(endpoint, "POST", Json(error_payload))
    except Exception:
        pass
        
    return user_tip

def _handle_blacklist(event, **kwargs):
    """黑名单用户消息处理"""
    user_id = event.user_id if hasattr(event, 'user_id') else None
    reason = kwargs.get('reason', '未指明原因')
    
    if USE_MARKDOWN:
        # Markdown模式：包含按钮
        message = f"<@{user_id}> 您已被列入黑名单，无法使用任何指令，如有误判，请点击下方反馈\n\n>原因：{reason}"
        
        # 添加反馈按钮
        buttons = event.button([
           event.rows([
                        {
                            'text': '加群',
                            'link': 'https://qm.qq.com/q/w5kFw95zDq',
                            'type': 0,
                            'style': 1
                        },
                        {
                            'text': '频道',
                            'link': 'https://pd.qq.com/s/4mrty8rgq?b=9',
                            'type': 0,
                            'style': 1
                        },
                        {
                            'text': '问卷',
                            'link': 'https://www.wjx.cn/vm/wVmvlOu.aspx',
                            'type': 0,
                            'style': 1
                        },
                    ])
                ])
        result = event.reply(message, buttons)
    else:
        # 非Markdown模式：纯文本消息，无按钮
        message = f"<@{user_id}> 您已被列入黑名单，无法使用任何指令，如有误判，请联系管理员\n\n原因：{reason}"
        result = event.reply(message)
    
    return result is not None

def _handle_group_blacklist(event, **kwargs):
    """群黑名单消息处理"""
    group_id = kwargs.get('group_id', '未知群组')
    
    message = "该群组已被列入黑名单，机器人已停止服务\n\n>如有疑问，请联系管理员"
    
    # 添加反馈按钮
    buttons = event.button([
       event.rows([
                    {
                        'text': '加群反馈',
                        'link': 'https://qm.qq.com/q/w5kFw95zDq',
                        'type': 0,
                        'style': 1
                    },
                    {
                        'text': '频道反馈',
                        'link': 'https://pd.qq.com/s/4mrty8rgq?b=9',
                        'type': 0,
                        'style': 1
                    },
                    {
                        'text': '问卷反馈',
                        'link': 'https://www.wjx.cn/vm/wVmvlOu.aspx',
                        'type': 0,
                        'style': 1
                    },
                ])
            ])
    
    result = event.reply(message, buttons)
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
        """
        统一的消息发送方法
        @param event: 消息事件对象
        @param msg_type: 消息类型，使用MSG_TYPE_*常量
        @param kwargs: 额外参数，根据消息类型不同而不同
        @return: 根据处理函数返回值，对于_handle_welcome返回(message, btn)元组，
                其他处理函数返回布尔值表示是否发送成功
        """
        try:
            # 查找对应的处理函数，如果没有则返回False
            handler = MESSAGE_HANDLERS.get(msg_type)
            if handler:
                return handler(event, **kwargs)
            return False
        except Exception as e:
            # 处理异常
            return False
    
    @staticmethod
    def register_handler(msg_type, handler_func):
        """
        注册新的消息处理函数
        @param msg_type: 消息类型
        @param handler_func: 处理函数
        """
        MESSAGE_HANDLERS[msg_type] = handler_func 