#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import binascii
import nacl.signing
from nacl.encoding import RawEncoder
import os
import sys

# 从配置中导入应用凭证
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import appid, secret

class Signs:
    """webhook签名校验"""
    def sign(self, data):
        """签名处理"""
        if isinstance(data, bytes):
            json_data = json.loads(data)
        else:
            json_data = json.loads(data)
            
        bot_secret = secret
        event_ts = json_data['d']['event_ts']
        plain_token = json_data['d']['plain_token']
        
        # 使用统一的签名生成函数
        result = self.generate_signature(bot_secret, event_ts, plain_token)
        
        # 返回签名结果
        return json.dumps(result)
    
    @staticmethod
    def generate_signature(bot_secret, event_ts, plain_token):
        """正确的签名生成函数
        
        参数:
            bot_secret: 机器人密钥
            event_ts: 事件时间戳
            plain_token: 原始令牌
            
        返回:
            包含plain_token和signature的字典
        """
        # 生成seed
        seed = bot_secret
        while len(seed.encode()) < nacl.signing.SEED_SIZE:
            seed += seed
        
        # 生成私钥
        private_key = nacl.signing.SigningKey(seed[:nacl.signing.SEED_SIZE].encode())
        
        # 构造消息并签名
        message = f"{event_ts}{plain_token}".encode()
        signature = private_key.sign(message, encoder=RawEncoder)
        
        # 返回签名结果
        return {
            "plain_token": plain_token,
            "signature": binascii.hexlify(signature.signature).decode()
        } 