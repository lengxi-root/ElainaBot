#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json, os, sys
from cryptography.hazmat.primitives.asymmetric import ed25519

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import appid, secret

class Signs:
    def sign(self, data):
        json_data = json.loads(data) if isinstance(data, bytes) else json.loads(data)
        bot_secret = secret
        event_ts = str(json_data['d']['event_ts'])
        plain_token = json_data['d']['plain_token']
        result = self.generate_signature(bot_secret, event_ts, plain_token)
        return json.dumps(result)
    
    @staticmethod
    def generate_signature(bot_secret, event_ts, plain_token):
        while len(bot_secret) < 32:
            bot_secret = (bot_secret + bot_secret)[:32]
        private_key = ed25519.Ed25519PrivateKey.from_private_bytes(bot_secret.encode())
        message = f"{event_ts}{plain_token}".encode()
        signature = private_key.sign(message).hex()
        return {"plain_token": plain_token, "signature": signature} 