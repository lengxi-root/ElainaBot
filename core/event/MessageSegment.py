#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
MessageSegment.py - 消息段构造器模块

用于构造QQ机器人消息的各种消息段类型，支持文本、图片、视频、音频、回复、
Markdown、按钮等多种消息类型，并提供组合多种消息段的辅助函数。
"""

# ===== 1. 标准库导入 =====
import json
from typing import Union, Dict, List, Any, Optional

# ===== 2. 第三方库导入 =====
# 无

# ===== 3. 自定义模块导入 =====
# 导入USE_MARKDOWN配置
try:
    from config import USE_MARKDOWN
except ImportError:
    USE_MARKDOWN = False

# 特殊JSON前缀标记，用于标识这是JSON格式的消息段
# 这个前缀会在MessageEvent内部被处理和移除
JSON_PREFIX = "<!-- JSON_SEGMENTS -->"

class Segment:
    """
    消息段构造器类
    用于为发送消息构造各种类型的消息段。
    """

    @staticmethod
    def text(text: str) -> Dict[str, str]:
        """
        创建一个文本消息段。
        :param text: 文本内容
        :return: 文本消息段字典
        """
        return {'type': 'text', 'text': text}

    @staticmethod
    def image(data: str) -> Dict[str, str]:
        """
        创建一个图片消息段。
        :param data: 图片数据 (URL、路径或 base64 字符串)
        :return: 图片消息段字典
        """
        return {'type': 'image', 'data': data}

    @staticmethod
    def video(data: str) -> Dict[str, str]:
        """
        创建一个视频消息段。
        :param data: 视频数据 (URL、路径或 base64 字符串)
        :return: 视频消息段字典
        """
        return {'type': 'video', 'data': data}

    @staticmethod
    def audio(data: str) -> Dict[str, str]:
        """
        创建一个音频消息段。
        :param data: 音频数据 (URL、路径或 base64 字符串)
        :return: 音频消息段字典
        """
        return {'type': 'audio', 'data': data}

    @staticmethod
    def record(data: str) -> Dict[str, str]:
        """
        创建一个语音 (record) 消息段。是 audio 的别名。
        :param data: 语音数据 (URL、路径或 base64 字符串)
        :return: 语音消息段字典
        """
        return {'type': 'record', 'data': data}

    @staticmethod
    def ark(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        创建一个 Ark 消息段。
        :param data: Ark 数据结构
        :return: Ark 消息段字典
        """
        return {'type': 'ark', 'data': data}

    @staticmethod
    def reply(id: str) -> Dict[str, str]:
        """
        创建一个回复消息段。
        :param id: 要回复的消息或事件的 ID
        :return: 回复消息段字典
        """
        return {'type': 'reply', 'id': id}

    @staticmethod
    def markdown(data: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
        """
        创建一个 Markdown 消息段。
        :param data: Markdown 内容字符串或数据结构
        :return: Markdown 消息段字典
        """
        return {'type': 'markdown', 'data': data}

    @staticmethod
    def keyboard(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        创建一个键盘消息段。
        :param data: 键盘数据 (例如：{'id': 'template_id'} 或完整的键盘结构)
        :return: 键盘消息段字典
        """
        return Segment.button(data)

    @staticmethod
    def button(id_or_data: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
        """
        创建一个按钮消息段。
        这将被解释为一个键盘。
        :param id_or_data: 键盘模板 ID (字符串) 或键盘数据结构 (字典)
        :return: 按钮消息段字典
        """
        if isinstance(id_or_data, str):
            return {'type': 'button', 'id': id_or_data}
        return {'type': 'button', 'data': id_or_data}

    @staticmethod
    def stream(stream_info: Dict[str, Any], inner_type: str, inner_data: Any) -> Dict[str, Any]:
        """
        创建一个流式消息段。
        :param stream_info: 包含流参数的字典 (例如：{'state': 1, 'id': None, 'index': 0, 'reset': False})
        :param inner_type: 内部消息的类型 ('text', 'markdown', 'keyboard', 'button')
        :param inner_data: 内部消息的数据
        :return: 流式消息段字典
        """
        stream_data_payload = {
            'stream': stream_info,
            'type': inner_type,
        }

        if inner_type == 'text':
            stream_data_payload['text'] = inner_data
        elif inner_type in ['markdown', 'keyboard']:
            stream_data_payload['data'] = inner_data
        elif inner_type == 'button':
            if isinstance(inner_data, str):
                stream_data_payload['id'] = inner_data  # 对于按钮类型，'id' 可以与 'type' 在同一层级
            else:
                stream_data_payload['data'] = inner_data
        else:
            # 记录不支持的内部类型警告
            import logging
            logging.warning(f"MessageSegment.stream: 不支持的内部类型 '{inner_type}' 用于流消息段。")

        return {'type': 'stream', 'data': stream_data_payload}

    @staticmethod
    def raw(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        创建一个原始消息段，允许使用自定义的 payload 结构。
        :param data: 原始 payload 数据
        :return: 原始消息段字典
        """
        return {'type': 'raw', 'data': data}

# 提供一个别名，使其更符合Python的命名习惯
MessageSegment = Segment 

def make_msg(*segments: Union[Dict[str, Any], str]) -> Dict[str, Any]:
    """
    构造完整的消息体，将多个消息段组合成一个消息。
    
    :param segments: 消息段列表或字符串。如果提供字符串，将自动转换为文本消息段。
    :return: 完整的消息体字典，可以直接传递给 event.reply(**make_msg(...))
    """
    processed_segments = []
    
    # 特殊处理键盘/按钮类型，它们需要放在msg_seq外部
    keyboard_data = None
    
    # 检查是否只有一个参数且为字符串，尝试解析为JSON消息段数组
    if len(segments) == 1 and isinstance(segments[0], str):
        try:
            # 如果字符串看起来像JSON数组（以[开头，以]结尾）
            content = segments[0].strip()
            if content.startswith('[') and content.endswith(']'):
                # 尝试解析为JSON
                json_data = json.loads(content)
                # 检查是否是数组且每个元素都有type字段
                if isinstance(json_data, list) and all('type' in item for item in json_data):
                    segments = json_data  # 使用解析后的JSON数组作为消息段
        except Exception:
            # 不是有效的JSON，按原样处理
            pass
    
    for segment in segments:
        if isinstance(segment, str):
            # 如果是字符串，转换为文本消息段
            processed_segments.append(Segment.text(segment))
        elif isinstance(segment, dict) and segment.get('type') in ['button', 'keyboard']:
            # 如果是按钮/键盘类型，提取出来单独处理
            keyboard_data = segment
        else:
            # 其他消息段直接添加
            processed_segments.append(segment)
    
    # 构建基本消息体，添加特殊前缀
    message = {
        'content': JSON_PREFIX + json.dumps(processed_segments, ensure_ascii=False)
    }
    
    # 如果有键盘/按钮数据，添加到消息体
    if keyboard_data is not None:
        if keyboard_data.get('type') == 'button':
            # 按钮类型
            if 'id' in keyboard_data:
                message['keyboard'] = {'id': keyboard_data['id']}
            else:
                message['keyboard'] = keyboard_data.get('data', {})
        else:
            # 键盘类型
            message['keyboard'] = keyboard_data.get('data', {})
    
    return message

def convert_for_reply(*segments: Union[Dict[str, Any], str]) -> Dict[str, Any]:
    """
    将消息段转换为event.reply()方法所需的参数。
    这个函数用于更直接地与MessageEvent.reply()方法集成。
    
    :param segments: 消息段列表或字符串
    :return: 包含content, buttons, media的字典，可以直接用于event.reply(**convert_for_reply(...))
    """
    # 检查是否只有一个参数且为字符串，尝试解析为JSON消息段数组
    if len(segments) == 1 and isinstance(segments[0], str):
        try:
            # 如果字符串看起来像JSON数组（以[开头，以]结尾）
            content = segments[0].strip()
            if content.startswith('[') and content.endswith(']'):
                # 尝试解析为JSON
                json_data = json.loads(content)
                # 检查是否是数组且每个元素都有type字段
                if isinstance(json_data, list) and all('type' in item for item in json_data):
                    return make_msg(*json_data)  # 使用解析后的JSON数组作为消息段
        except Exception:
            # 不是有效的JSON，按原样处理
            pass
    
    # 提取文本内容
    text_content = []
    buttons = None
    media_list = []
    has_complex_segments = False  # 标记是否有复杂消息段（如图片、回复等）
    
    for segment in segments:
        if isinstance(segment, str):
            text_content.append(segment)
        elif isinstance(segment, dict):
            segment_type = segment.get('type')
            
            if segment_type in ['button', 'keyboard']:
                # 处理按钮/键盘
                if segment_type == 'button':
                    if 'id' in segment:
                        buttons = {'id': segment['id']}
                    else:
                        buttons = segment.get('data', {})
                else:
                    buttons = segment.get('data', {})
            elif segment_type in ['image', 'video', 'audio', 'record']:
                # 处理媒体
                media_list.append(segment)
                has_complex_segments = True
            elif segment_type == 'text':
                # 处理文本
                text_content.append(segment.get('text', ''))
            elif segment_type == 'reply':
                # 有回复段，标记为复杂消息
                has_complex_segments = True
                # 回复段不添加到media_list，而是通过make_msg处理
            elif segment_type == 'markdown':
                # 有Markdown段，标记为复杂消息
                has_complex_segments = True
                # Markdown段不添加到media_list，而是通过make_msg处理
    
    # 如果有复杂消息段或者启用了全局Markdown但我们想用普通文本，使用make_msg构建
    if has_complex_segments or (USE_MARKDOWN and not any(isinstance(s, dict) and s.get('type') == 'markdown' for s in segments)):
        return make_msg(*segments)
    
    # 组合所有文本内容
    content = ' '.join(text_content)
    
    # 构建返回结果
    result = {
        'content': content,
        'keyboard': buttons,  # 使用keyboard与MessageEvent.py一致
        'media': media_list if media_list else None
    }
    
    return result 

def process_json_message(json_str: str) -> Dict[str, Any]:
    """
    直接处理JSON格式的消息段字符串，转换为结构化消息。
    这个函数用于处理纯JSON格式的消息段，避免被当作普通文本处理。
    
    :param json_str: JSON格式的消息段字符串
    :return: 可用于event.reply()方法的消息字典
    """
    try:
        # 确保输入是字符串并且看起来像JSON
        if not isinstance(json_str, str):
            return {'content': str(json_str)}
            
        json_str = json_str.strip()
        if not (json_str.startswith('[') and json_str.endswith(']')):
            return {'content': json_str}
            
        # 解析JSON
        segments = json.loads(json_str)
        if not isinstance(segments, list) or not all('type' in item for item in segments):
            return {'content': json_str}
            
        # 处理各种消息段
        text_segments = []
        media_segment = None
        buttons_data = None
        reply_id = None
        
        for segment in segments:
            segment_type = segment.get('type')
            
            if segment_type == 'text':
                text_segments.append(segment.get('text', ''))
            elif segment_type in ['image', 'video', 'audio', 'record'] and not media_segment:
                media_segment = segment
            elif segment_type in ['button', 'keyboard'] and not buttons_data:
                if segment_type == 'button':
                    if 'id' in segment:
                        buttons_data = {'id': segment['id']}
                    else:
                        buttons_data = segment.get('data', {})
                else:
                    buttons_data = segment.get('data', {})
            elif segment_type == 'reply' and not reply_id:
                reply_id = segment.get('id')
        
        # 构建结果消息
        result = {}
        
        # 如果有回复ID，需要构建特殊格式
        if reply_id:
            # 使用JSON_PREFIX标记这是JSON段
            processed_segments = [{'type': 'reply', 'id': reply_id}]
            for text in text_segments:
                processed_segments.append({'type': 'text', 'text': text})
            if media_segment:
                processed_segments.append(media_segment)
                
            result['content'] = JSON_PREFIX + json.dumps(processed_segments, ensure_ascii=False)
            if buttons_data:
                result['keyboard'] = buttons_data  # 使用keyboard与MessageEvent.py一致
            return result
        
        # 普通消息处理
        if media_segment:
            # 媒体消息
            result['content'] = ' '.join(text_segments)
            result['media'] = [media_segment]
        else:
            # 纯文本消息
            result['content'] = ' '.join(text_segments)
        
        if buttons_data:
            result['keyboard'] = buttons_data  # 使用keyboard与MessageEvent.py一致
            
        return result
    except Exception as e:
        # 解析失败，返回原始内容
        import logging
        logging.error(f"Failed to process JSON message: {str(e)}")
        return {'content': json_str} 