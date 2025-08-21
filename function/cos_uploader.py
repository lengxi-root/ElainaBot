#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
腾讯云COS对象存储上传模块
提供文件上传到腾讯云COS的功能
"""

import os
import logging
import hashlib
import mimetypes
from datetime import datetime
from typing import Optional, Union, Dict, Any
from io import BytesIO

try:
    from qcloud_cos import CosConfig, CosS3Client
    from qcloud_cos.cos_exception import CosServiceError, CosClientError
except ImportError:
    CosConfig = None
    CosS3Client = None
    CosServiceError = Exception
    CosClientError = Exception

import config

logger = logging.getLogger(__name__)


class COSUploader:
    """腾讯云COS上传器"""
    
    def __init__(self):
        """初始化COS上传器"""
        self.config = config.COS_CONFIG
        self.client = None
        
        if not self.config.get('enabled', False):
            logger.warning("COS上传功能已禁用")
            return
            
        if not all([CosConfig, CosS3Client]):
            logger.error("COS SDK未安装，请运行: pip install cos-python-sdk-v5")
            return
            
        self._init_client()
    
    def _init_client(self):
        """初始化COS客户端"""
        try:
            # 配置COS客户端
            cos_config = CosConfig(
                Region=self.config['region'],
                SecretId=self.config['secret_id'],
                SecretKey=self.config['secret_key'],
                Scheme='https'
            )
            
            self.client = CosS3Client(cos_config)
            logger.info(f"COS客户端初始化成功，区域: {self.config['region']}")
            
        except Exception as e:
            logger.error(f"COS客户端初始化失败: {e}")
            self.client = None
    
    def _validate_file(self, file_data: bytes, filename: str) -> bool:
        """验证文件"""
        # 检查文件大小
        if len(file_data) > self.config.get('max_file_size', 100 * 1024 * 1024):
            logger.error(f"文件 {filename} 大小超过限制")
            return False
        
        # 检查文件扩展名
        file_ext = filename.split('.')[-1].lower() if '.' in filename else ''
        allowed_extensions = self.config.get('allowed_extensions', [])
        if allowed_extensions and file_ext not in allowed_extensions:
            logger.error(f"文件 {filename} 扩展名 {file_ext} 不被允许")
            return False
        
        return True
    
    def _generate_cos_key(self, filename: str, custom_path: str = None, user_id: str = None) -> str:
        """生成COS对象键"""
        # 如果指定了自定义路径，直接使用
        if custom_path:
            return custom_path.replace('\\', '/')
        
        # 生成时间戳
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # 使用默认路径前缀
        path_parts = [self.config.get('upload_path_prefix', 'mlog/')]
        if user_id:
            path_parts.append(f"{user_id}/")
        
        # 添加时间戳目录
        path_parts.append(f"{timestamp}/")
        
        # 构建完整路径
        cos_key = ''.join(path_parts) + filename
        return cos_key.replace('\\', '/')  # 确保使用正斜杠
    
    def _get_content_type(self, filename: str) -> str:
        """获取文件MIME类型"""
        content_type, _ = mimetypes.guess_type(filename)
        return content_type or 'application/octet-stream'
    
    def upload_file(self, 
                   file_data: Union[bytes, BytesIO], 
                   filename: str,
                   user_id: str = None,
                   custom_path: str = None) -> Optional[Dict[str, Any]]:
        """
        上传文件到COS
        
        Args:
            file_data: 文件数据（字节或BytesIO对象）
            filename: 文件名
            user_id: 用户ID（可选，用于分目录存储）
            custom_path: 自定义路径（可选，覆盖默认路径生成）
            
        Returns:
            上传成功返回包含文件信息的字典，失败返回None
        """
        if not self.config.get('enabled', False):
            logger.error("COS上传功能未启用")
            return None
            
        if not self.client:
            logger.error("COS客户端未初始化")
            return None
        
        try:
            # 如果是BytesIO对象，读取数据
            if isinstance(file_data, BytesIO):
                file_bytes = file_data.getvalue()
            else:
                file_bytes = file_data
            
            # 验证文件
            if not self._validate_file(file_bytes, filename):
                return None
            
            # 生成COS键
            cos_key = self._generate_cos_key(filename, custom_path, user_id)
            
            # 获取内容类型
            content_type = self._get_content_type(filename)
            
            # 创建上传对象
            file_stream = BytesIO(file_bytes)
            
            # 上传文件
            response = self.client.put_object(
                Bucket=self.config['bucket_name'],
                Body=file_stream,
                Key=cos_key,
                StorageClass=self.config.get('storage_class', 'STANDARD'),
                ContentType=content_type,
                Metadata={
                    'original-filename': filename,
                    'upload-time': datetime.now().isoformat(),
                    'user-id': user_id or 'anonymous'
                }
            )
            
            # 生成访问URL
            base_url = f"https://{self.config['bucket_name']}.cos.{self.config['region']}.myqcloud.com"
            if self.config.get('domain'):
                base_url = f"https://{self.config['domain']}"
            
            file_url = f"{base_url}/{cos_key}"
            
            # 返回上传结果
            result = {
                'success': True,
                'cos_key': cos_key,
                'file_url': file_url,
                'etag': response.get('ETag', '').strip('"'),
                'filename': filename,
                'file_size': len(file_bytes),
                'content_type': content_type,
                'upload_time': datetime.now().isoformat(),
                'user_id': user_id
            }
            
            logger.info(f"文件上传成功: {filename} -> {cos_key}")
            return result
            
        except CosServiceError as e:
            logger.error(f"COS服务错误: {e}")
            return None
        except CosClientError as e:
            logger.error(f"COS客户端错误: {e}")
            return None
        except Exception as e:
            logger.error(f"上传文件时发生未知错误: {e}")
            return None
    
    def upload_local_file(self, 
                         local_path: str,
                         user_id: str = None,
                         custom_filename: str = None,
                         custom_path: str = None) -> Optional[Dict[str, Any]]:
        """
        上传本地文件到COS
        
        Args:
            local_path: 本地文件路径
            user_id: 用户ID
            custom_filename: 自定义文件名
            custom_path: 自定义COS路径
            
        Returns:
            上传成功返回包含文件信息的字典，失败返回None
        """
        if not os.path.exists(local_path):
            logger.error(f"本地文件不存在: {local_path}")
            return None
        
        try:
            with open(local_path, 'rb') as f:
                file_data = f.read()
            
            filename = custom_filename or os.path.basename(local_path)
            return self.upload_file(file_data, filename, user_id, custom_path)
            
        except Exception as e:
            logger.error(f"读取本地文件失败: {e}")
            return None
    
    def delete_file(self, cos_key: str) -> bool:
        """
        删除COS中的文件
        
        Args:
            cos_key: COS对象键
            
        Returns:
            删除成功返回True，失败返回False
        """
        if not self.client:
            logger.error("COS客户端未初始化")
            return False
        
        try:
            self.client.delete_object(
                Bucket=self.config['bucket_name'],
                Key=cos_key
            )
            logger.info(f"文件删除成功: {cos_key}")
            return True
            
        except CosServiceError as e:
            logger.error(f"删除文件失败: {e}")
            return False
        except Exception as e:
            logger.error(f"删除文件时发生未知错误: {e}")
            return False
    
    def get_file_info(self, cos_key: str) -> Optional[Dict[str, Any]]:
        """
        获取COS中文件的信息
        
        Args:
            cos_key: COS对象键
            
        Returns:
            文件信息字典或None
        """
        if not self.client:
            logger.error("COS客户端未初始化")
            return None
        
        try:
            response = self.client.head_object(
                Bucket=self.config['bucket_name'],
                Key=cos_key
            )
            
            return {
                'cos_key': cos_key,
                'content_length': response.get('Content-Length'),
                'content_type': response.get('Content-Type'),
                'etag': response.get('ETag', '').strip('"'),
                'last_modified': response.get('Last-Modified'),
                'metadata': response.get('Metadata', {})
            }
            
        except CosServiceError as e:
            logger.error(f"获取文件信息失败: {e}")
            return None
        except Exception as e:
            logger.error(f"获取文件信息时发生未知错误: {e}")
            return None
    
    def list_files(self, prefix: str = None, max_keys: int = 1000) -> Optional[list]:
        """
        列出COS中的文件
        
        Args:
            prefix: 文件前缀过滤
            max_keys: 最大返回数量
            
        Returns:
            文件列表或None
        """
        if not self.client:
            logger.error("COS客户端未初始化")
            return None
        
        try:
            kwargs = {
                'Bucket': self.config['bucket_name'],
                'MaxKeys': max_keys
            }
            if prefix:
                kwargs['Prefix'] = prefix
            
            response = self.client.list_objects(**kwargs)
            
            if 'Contents' not in response:
                return []
            
            files = []
            for obj in response['Contents']:
                files.append({
                    'key': obj['Key'],
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'],
                    'etag': obj['ETag'].strip('"'),
                    'storage_class': obj.get('StorageClass', 'STANDARD')
                })
            
            return files
            
        except CosServiceError as e:
            logger.error(f"列出文件失败: {e}")
            return None
        except Exception as e:
            logger.error(f"列出文件时发生未知错误: {e}")
            return None


# 全局实例
cos_uploader = COSUploader()


def upload_file(file_data: Union[bytes, BytesIO], 
               filename: str,
               user_id: str = None,
               custom_path: str = None) -> Optional[Dict[str, Any]]:
    """便捷的文件上传函数"""
    return cos_uploader.upload_file(file_data, filename, user_id, custom_path)


def upload_local_file(local_path: str,
                     user_id: str = None,
                     custom_filename: str = None,
                     custom_path: str = None) -> Optional[Dict[str, Any]]:
    """便捷的本地文件上传函数"""
    return cos_uploader.upload_local_file(local_path, user_id, custom_filename, custom_path)


def simple_upload(file_data: Union[bytes, BytesIO], filename: str, upload_path: str = None) -> Optional[str]:
    """
    简单上传文件，返回访问链接
    
    Args:
        file_data: 文件数据
        filename: 文件名
        upload_path: 上传路径（插件指定），如果为None则使用默认mlog路径
        
    Returns:
        成功返回文件访问链接，失败返回None
    """
    result = cos_uploader.upload_file(file_data, filename, custom_path=upload_path)
    return result['file_url'] if result and result.get('success') else None


def get_upload_url(cos_key: str) -> str:
    """
    根据COS键获取访问链接
    
    Args:
        cos_key: COS对象键
        
    Returns:
        文件访问链接
    """
    if not cos_uploader.config.get('enabled', False):
        return ""
    
    # 生成访问URL
    base_url = f"https://{cos_uploader.config['bucket_name']}.cos.{cos_uploader.config['region']}.myqcloud.com"
    if cos_uploader.config.get('domain'):
        base_url = f"https://{cos_uploader.config['domain']}"
    
    return f"{base_url}/{cos_key}"


def delete_by_url(file_url: str) -> bool:
    """
    根据文件URL删除文件
    
    Args:
        file_url: 文件访问链接
        
    Returns:
        删除成功返回True，失败返回False
    """
    if not file_url:
        return False
    
    # 从URL中提取COS键
    try:
        # 处理自定义域名和默认域名
        if cos_uploader.config.get('domain'):
            base_url = f"https://{cos_uploader.config['domain']}/"
        else:
            base_url = f"https://{cos_uploader.config['bucket_name']}.cos.{cos_uploader.config['region']}.myqcloud.com/"
        
        if file_url.startswith(base_url):
            cos_key = file_url[len(base_url):]
            return cos_uploader.delete_file(cos_key)
    except Exception as e:
        logger.error(f"从URL提取COS键失败: {e}")
    
    return False