from typing import Optional, Dict, Any

class Segment:
    @staticmethod
    def text(text: str) -> Dict[str, str]:
        """创建文本消息段"""
        return {'type': 'text', 'text': text}

    @staticmethod
    def image(file: str, cache: Optional[str] = None) -> Dict[str, str]:
        """创建图片消息段"""
        data = {'type': 'image', 'file': file}
        if cache is not None:
            data['cache'] = cache
        return data

    @staticmethod
    def at(user_id: str) -> Dict[str, str]:
        """创建At消息段"""
        return {'type': 'at', 'user_id': user_id}

    @staticmethod
    def raw(type_: str, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """通用消息段构造方法"""
        if data is None:
            data = {}
        return {'type': type_, **data} 