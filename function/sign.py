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
        
        # 生成seed
        seed = bot_secret
        while len(seed.encode()) < nacl.signing.SEED_SIZE:
            seed += seed
        
        # 生成私钥
        private_key = nacl.signing.SigningKey(seed[:nacl.signing.SEED_SIZE].encode())
        
        # 生成签名
        signature_message = f"{json_data['d']['event_ts']}{json_data['d']['plain_token']}".encode()
        signature = private_key.sign(signature_message, encoder=RawEncoder)
        
        # 返回签名结果
        return json.dumps({
            'plain_token': json_data['d']['plain_token'],
            'signature': binascii.hexlify(signature.signature).decode()
        }) 