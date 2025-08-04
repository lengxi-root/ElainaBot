#!/usr/bin/env python
# -*- coding: utf-8 -*-

from core.plugin.PluginManager import Plugin
import requests
from function.httpx_pool import get_binary_content
import io

# 添加获取二进制内容的函数
def get_binary_content_fallback(url):
    """下载URL内容并返回二进制数据"""
    response = requests.get(url)
    response.raise_for_status()
    return response.content

class image_plugin(Plugin):
    priority = 10
    
    @staticmethod
    def get_regex_handlers():
        return {
            # 图片发送示例
            r'^图片$': 'send_advanced_image',
            r'^普通图片$': 'send_simple_image'
        }
    
    @staticmethod
    def send_advanced_image(event):
        image_url = "https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0"
        
        try:
            # 使用自定义模板发送带图片的消息
            template_id = "102321943_1747061997"
            
            params = [
                {
                "key": "px",
                "values": ["珊瑚宫心海 #1200px #2133px"]
            }, {
                "key": "url",
                "values": [image_url]
            }, {
                "key": "text",
                "values": ["\r\r>ElainaBot Markdown图片例子"]
            }]
            
            custom_template = {
                "custom_template_id": template_id,
                "params": params
            }
            
            event.reply(custom_template)
                
        except Exception as e:
            # 发送失败时返回错误信息
            event.reply(f"图片发送失败: {str(e)}") 

    @staticmethod
    def send_simple_image(event):
        """发送普通图片示例"""
        try:
            # 使用示例URL
            image_url = "https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0"
            
            try:
                image_data = get_binary_content(image_url)
            except Exception:
                image_data = get_binary_content_fallback(image_url)
            event.reply("这是一张普通图片", media=image_data)
            
        except Exception as e:
            event.reply(f"图片发送失败: {str(e)}")