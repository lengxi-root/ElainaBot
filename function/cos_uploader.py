#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import logging
import mimetypes
from datetime import datetime
from typing import Optional, Union, Dict, Any
from io import BytesIO

# 抑制SDK自身INFO日志
logging.getLogger('qcloud_cos').setLevel(logging.WARNING)

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
    def __init__(self):
        self.config = config.COS_CONFIG
        self.client = None
        
        if self.config.get('enabled', False) and all([CosConfig, CosS3Client]):
            self._init_client()
    
    def _init_client(self):
        try:
            cos_config = CosConfig(
                Region=self.config['region'],
                SecretId=self.config['secret_id'],
                SecretKey=self.config['secret_key'],
                Scheme='https'
            )
            self.client = CosS3Client(cos_config)
        except Exception as e:
            logger.error(f"COS初始化失败: {e}")
    
    def _validate_file(self, file_data: bytes, filename: str) -> bool:
        if len(file_data) > self.config.get('max_file_size', 100 * 1024 * 1024):
            logger.error(f"文件 {filename} 大小超限")
            return False
        
        file_ext = filename.split('.')[-1].lower() if '.' in filename else ''
        if self.config.get('allowed_extensions') and file_ext not in self.config['allowed_extensions']:
            logger.error(f"文件 {filename} 格式不允许")
            return False
        
        return True
    
    def _generate_cos_key(self, filename: str, custom_path: str = None, user_id: str = None) -> str:
        if custom_path:
            return custom_path.replace('\\', '/')
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        path_parts = [self.config.get('upload_path_prefix', 'mlog/')]
        if user_id:
            path_parts.append(f"{user_id}/")
        path_parts.append(f"{timestamp}/")
        
        return ''.join(path_parts) + filename.replace('\\', '/')
    
    def _get_content_type(self, filename: str) -> str:
        content_type, _ = mimetypes.guess_type(filename)
        return content_type or 'application/octet-stream'
    
    def upload_file(self, 
                   file_data: Union[bytes, BytesIO], 
                   filename: str,
                   user_id: str = None,
                   custom_path: str = None) -> Optional[Dict[str, Any]]:
        if not self.config.get('enabled', False):
            logger.error("COS未启用")
            return None
            
        if not self.client:
            logger.error("COS客户端未初始化")
            return None
        
        try:
            file_bytes = file_data.getvalue() if isinstance(file_data, BytesIO) else file_data
            
            if not self._validate_file(file_bytes, filename):
                return None
            
            cos_key = self._generate_cos_key(filename, custom_path, user_id)
            file_stream = BytesIO(file_bytes)
            
            response = self.client.put_object(
                Bucket=self.config['bucket_name'],
                Body=file_stream,
                Key=cos_key,
                StorageClass=self.config.get('storage_class', 'STANDARD'),
                ContentType=self._get_content_type(filename),
                Metadata={
                    'original-filename': filename,
                    'upload-time': datetime.now().isoformat(),
                    'user-id': user_id or 'anonymous'
                }
            )
            
            base_url = f"https://{self.config['domain']}" if self.config.get('domain') else \
                      f"https://{self.config['bucket_name']}.cos.{self.config['region']}.myqcloud.com"
            
            return {
                'success': True,
                'cos_key': cos_key,
                'file_url': f"{base_url}/{cos_key}",
                'etag': response.get('ETag', '').strip('"'),
                'filename': filename,
                'file_size': len(file_bytes)
            }
            
        except (CosServiceError, CosClientError) as e:
            logger.error(f"COS上传失败 [{filename}]: {str(e)[:100]}")
        except Exception as e:
            logger.error(f"上传失败 [{filename}]: {e}")
        
        return None
    
    def upload_local_file(self, 
                         local_path: str,
                         user_id: str = None,
                         custom_filename: str = None,
                         custom_path: str = None) -> Optional[Dict[str, Any]]:
        if not os.path.exists(local_path):
            logger.error(f"文件不存在: {local_path}")
            return None
        
        try:
            with open(local_path, 'rb') as f:
                return self.upload_file(f.read(), 
                                      custom_filename or os.path.basename(local_path),
                                      user_id, 
                                      custom_path)
        except Exception as e:
            logger.error(f"读取文件失败 [{local_path}]: {e}")
            return None
    
    def delete_file(self, cos_key: str) -> bool:
        if not self.client:
            logger.error("COS客户端未初始化")
            return False
        
        try:
            self.client.delete_object(Bucket=self.config['bucket_name'], Key=cos_key)
            return True
        except Exception as e:
            logger.error(f"删除失败 [{cos_key}]: {e}")
            return False
    
    def get_file_info(self, cos_key: str) -> Optional[Dict[str, Any]]:
        if not self.client:
            logger.error("COS客户端未初始化")
            return None
        
        try:
            response = self.client.head_object(Bucket=self.config['bucket_name'], Key=cos_key)
            return {
                'cos_key': cos_key,
                'content_length': response.get('Content-Length'),
                'content_type': response.get('Content-Type'),
                'last_modified': response.get('Last-Modified')
            }
        except Exception as e:
            logger.error(f"获取信息失败 [{cos_key}]: {e}")
            return None
    
    def list_files(self, prefix: str = None, max_keys: int = 1000) -> Optional[list]:
        if not self.client:
            logger.error("COS客户端未初始化")
            return None
        
        try:
            kwargs = {'Bucket': self.config['bucket_name'], 'MaxKeys': max_keys}
            if prefix:
                kwargs['Prefix'] = prefix
            
            response = self.client.list_objects(** kwargs)
            if 'Contents' not in response:
                return []
            
            return [{
                'key': obj['Key'],
                'size': obj['Size'],
                'last_modified': obj['LastModified']
            } for obj in response['Contents']]
            
        except Exception as e:
            logger.error(f"列出文件失败: {e}")
            return None


cos_uploader = COSUploader()


def upload_file(file_data: Union[bytes, BytesIO], 
               filename: str,
               user_id: str = None,
               custom_path: str = None) -> Optional[Dict[str, Any]]:
    return cos_uploader.upload_file(file_data, filename, user_id, custom_path)


def upload_local_file(local_path: str,
                     user_id: str = None,
                     custom_filename: str = None,
                     custom_path: str = None) -> Optional[Dict[str, Any]]:
    return cos_uploader.upload_local_file(local_path, user_id, custom_filename, custom_path)


def simple_upload(file_data: Union[bytes, BytesIO], filename: str, upload_path: str = None) -> Optional[str]:
    result = cos_uploader.upload_file(file_data, filename, custom_path=upload_path)
    return result['file_url'] if result and result.get('success') else None


def get_upload_url(cos_key: str) -> str:
    if not cos_uploader.config.get('enabled', False):
        return ""
    
    base_url = f"https://{cos_uploader.config['domain']}" if cos_uploader.config.get('domain') else \
              f"https://{cos_uploader.config['bucket_name']}.cos.{cos_uploader.config['region']}.myqcloud.com"
    return f"{base_url}/{cos_key}"


def delete_by_url(file_url: str) -> bool:
    if not file_url:
        return False
    
    try:
        base_url = f"https://{cos_uploader.config['domain']}/" if cos_uploader.config.get('domain') else \
                  f"https://{cos_uploader.config['bucket_name']}.cos.{cos_uploader.config['region']}.myqcloud.com/"
        
        if file_url.startswith(base_url):
            return cos_uploader.delete_file(file_url[len(base_url):])
    except Exception as e:
        logger.error(f"URL解析失败: {e}")
    
    return False
