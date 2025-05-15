class Config:
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self._initialized = True
            self.appid = "机器人appid"
            self.secret = "机器人密钥"
            
    @property
    def get_appid(self):
        return self.appid
        
    @property
    def get_secret(self):
        return self.secret 