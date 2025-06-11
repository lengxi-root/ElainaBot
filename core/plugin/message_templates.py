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
MSG_TYPE_GROUP_ONLY = 'group_only'          # 群聊专用命令提示
MSG_TYPE_DEFAULT = 'default'                # 默认回复
MSG_TYPE_OWNER_ONLY = 'owner_only'          # 主人专属命令提示
MSG_TYPE_MAINTENANCE = 'maintenance'        # 维护模式回复
MSG_TYPE_API_ERROR = 'api_error'            # API错误提示消息

# 消息类型映射常量
GROUP_MESSAGE = 'GROUP_AT_MESSAGE_CREATE'      # 群消息类型
DIRECT_MESSAGE = 'C2C_MESSAGE_CREATE'          # 私聊消息类型
INTERACTION = 'INTERACTION_CREATE'             # 按钮交互消息类型
CHANNEL_MESSAGE = 'AT_MESSAGE_CREATE'          # 频道消息类型
GROUP_ADD_ROBOT = 'GROUP_ADD_ROBOT'            # 被拉进群事件类型

# 各个模板处理函数，便于单独修改

def _handle_welcome(event, **kwargs):
    """群欢迎消息处理"""
    message = "![感谢 #1755px #2048px](https://gd-hbimg.huaban.com/d8b5c087d33e7d25835db96adab5f226227e943a165000-gzpWLe)\n__「你绝不是只身一人」 「我一直在你身边。」\n今朝依旧，今后亦然。__\n\n>大家好，我是有着沉鱼落雁般美貌的灰之魔女伊蕾娜！\n\n>可以为群内提供各种各样的群娱互动，与一些高质量图库功能，欢迎大家使用！\n***\n\n>注:所有指令必须_[@伊蕾娜]_才能使用,可以先尝试发送娱乐菜单，有按钮可以一键发送命令使用哦~\n"
    
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
    return result is not None

def _handle_user_welcome(event, **kwargs):
    """新用户欢迎消息处理"""
    user_id = event.user_id if hasattr(event, 'user_id') else None
    user_count = kwargs.get('user_count', 1)
    
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
    return result is not None

def _handle_group_only(event, **kwargs):
    """群聊专用命令提示处理"""
    user_id = event.user_id if hasattr(event, 'user_id') else None
    
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
    return result is not None

def _handle_default(event, **kwargs):
    """默认回复处理"""
    user_id = event.user_id if hasattr(event, 'user_id') else None
    
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
            'data': 'https://www.wjx.cn/vm/rJ1ZKHn.aspx',
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
    
    result = event.reply(f"![错误指令 #1360px #680px](https://gd-hbimg.huaban.com/53f695e975a52018a87ab8dc21bffff16da658ff7c6d7-fDXTPP)\n\n><@{user_id}> ", btn)
    return result is not None

def _handle_owner_only(event, **kwargs):
    """主人专属命令提示处理"""
    user_id = event.user_id if hasattr(event, 'user_id') else None
    result = event.reply(f"<@{user_id}> 暂无权限，你无法操作此命令")
    return result is not None

def _handle_maintenance(event, **kwargs):
    """维护模式回复处理"""
    message = "系统正在维护中，请稍后再试...\n>当前功能暂时不可用，维护完成后将自动恢复"
    
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
    return result is not None

def _handle_api_error(event, **kwargs):
    """API错误消息处理"""
    error_code = kwargs.get('error_code')
    trace_id = kwargs.get('trace_id')
    endpoint = kwargs.get('endpoint')
    
    # 根据不同错误码定制错误提示
    if error_code == 40034006:
        user_tip = f"\n消息发送失败\n\n>code:{error_code}\ntrace_id:{trace_id}\n注：消息违规，请截图选一种方式反馈"
        show_feedback_buttons = True
    elif error_code == 40054017:
        user_tip = f"\n消息发送失败\n\n>code:{error_code}\ntrace_id:{trace_id}\n注：消息被拦截，可能因你的群昵称导致，请更换群昵称尝试，如果还是不可用则截图反馈"
        show_feedback_buttons = True
    elif error_code == 50015006:
        user_tip = f"\n消息发送失败\n\n>code:{error_code}\ntrace_id:{trace_id}\n注：系统繁忙，稍后重试"
        show_feedback_buttons = False
    elif error_code == 40054010:
        user_tip = f"\n消息发送失败\n\n>code:{error_code}\ntrace_id:{trace_id}\n注：禁止发送url，请截图选一种方式反馈"
        show_feedback_buttons = True
    else:
        user_tip = f"\n消息发送失败\n\n>code:{error_code}\ntrace_id:{trace_id}\n注：出现错误，请截图进行反馈"
        show_feedback_buttons = True
    
    try:
        error_payload = {
            "msg_type": 2,
            "msg_seq": random.randint(10000, 999999),
            "markdown": {
                "content": user_tip
            }
        }
        
        # 只在需要时添加反馈按钮
        if show_feedback_buttons:
            feedback_button = event.button([
                event.rows([
                    {
                        'text': '加群(推荐)',
                        'link': 'https://qm.qq.com/q/w5kFw95zDq',
                        'type': 0,
                        'style': 1
                    },
                    {
                        'text': '问卷(较慢)',
                        'link': 'https://www.wjx.cn/vm/rJ1ZKHn.aspx',
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
            error_payload["event_id"] = event.get('id') or event.get('d/id') or ""
        elif event.message_type == CHANNEL_MESSAGE:
            error_payload["msg_id"] = event.get('d/id')
            
        # 发送API错误提示
        BOTAPI(endpoint, "POST", Json(error_payload))
    except Exception:
        pass
        
    return user_tip

# 消息处理器映射表
MESSAGE_HANDLERS = {
    MSG_TYPE_WELCOME: _handle_welcome,
    MSG_TYPE_USER_WELCOME: _handle_user_welcome,
    MSG_TYPE_GROUP_ONLY: _handle_group_only,
    MSG_TYPE_DEFAULT: _handle_default,
    MSG_TYPE_OWNER_ONLY: _handle_owner_only,
    MSG_TYPE_MAINTENANCE: _handle_maintenance,
    MSG_TYPE_API_ERROR: _handle_api_error,
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
        @return: 是否发送成功
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