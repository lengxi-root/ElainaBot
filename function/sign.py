import json
import nacl.signing
from config import Config

class Signs:
    def sign(self, data):
        """webhook签名校验"""
        config = Config()
        bot_secret = config.get_secret
        
        if isinstance(data, bytes):
            payload = json.loads(data.decode())
        else:
            payload = json.loads(data)
            
        # 生成种子
        seed = bot_secret.encode()
        while len(seed) < nacl.signing.SEED_SIZE:
            seed += seed
            
        # 生成私钥
        signing_key = nacl.signing.SigningKey(seed[:nacl.signing.SEED_SIZE])
        
        # 生成签名
        message = (payload['d']['event_ts'] + payload['d']['plain_token']).encode()
        signature = signing_key.sign(message).signature
        
        return json.dumps({
            'plain_token': payload['d']['plain_token'],
            'signature': signature.hex()
        }) 