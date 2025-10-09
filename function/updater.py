#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import shutil
import zipfile
import logging
import requests
import threading
import traceback
from datetime import datetime
from pathlib import Path
import fnmatch

class FrameworkUpdater:
    
    def __init__(self, config=None):
        self.base_dir = Path(__file__).parent.parent.absolute()
        self.version_file = self.base_dir / "data" / "version.json"
        self.config = config or self._load_config()
        self.logger = logging.getLogger('FrameworkUpdater')
        (self.base_dir / "data").mkdir(exist_ok=True)
        self.current_version = self._load_version()
        self._update_thread = None
        self._stop_event = threading.Event()
        self.progress_callback = None
        self.current_progress = {'stage': 'idle', 'message': '未开始', 'progress': 0, 'is_updating': False}
    
    def _report_progress(self, stage, message, progress=0):
        self.current_progress = {'stage': stage, 'message': message, 'progress': progress, 'is_updating': stage not in ['idle', 'completed', 'failed']}
        if self.progress_callback:
            try:
                self.progress_callback(self.current_progress)
            except Exception as e:
                self.logger.error(f"进度回调失败: {e}")
    
    def get_progress(self):
        return self.current_progress.copy()
    
    def _load_config(self):
        try:
            from config import AUTO_UPDATE_CONFIG
            config = AUTO_UPDATE_CONFIG.copy()
        except:
            config = {'enabled': False, 'check_interval': 1800, 'auto_update': False, 'backup_enabled': True, 'skip_files': ["config.py", "data/", "plugins/"]}
        config['update_api'] = "https://i.elaina.vin/api/elainabot/"
        config['backup_dir'] = "data/backup"
        return config
    
    def _load_version(self):
        if self.version_file.exists():
            try:
                with open(self.version_file, 'r', encoding='utf-8') as f:
                    return json.load(f).get('version', 'unknown')
            except:
                pass
        return 'unknown'
    
    def _save_version(self, version):
        try:
            data = {'version': version, 'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'updated_from': self.current_version}
            with open(self.version_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.current_version = version
            return True
        except Exception as e:
            self.logger.error(f"保存版本信息失败: {e}")
            return False
    
    def get_version_info(self):
        if self.version_file.exists():
            try:
                with open(self.version_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return {'version': self.current_version, 'update_time': 'unknown', 'updated_from': 'unknown'}
    
    def check_for_updates(self):
        try:
            response = requests.get(self.config['update_api'], timeout=10)
            response.raise_for_status()
            commits = response.json()
            if not commits:
                return {'has_update': False, 'latest_version': self.current_version, 'current_version': self.current_version, 'changelog': [], 'error': '无法获取更新信息'}
            latest_version = commits[0].get('sha', '')[:7]
            has_update = (self.current_version != latest_version and self.current_version != 'unknown') or self.current_version == 'unknown'
            return {'has_update': has_update, 'latest_version': latest_version, 'current_version': self.current_version, 'changelog': commits[:10], 'error': None}
        except Exception as e:
            self.logger.error(f"检查更新失败: {e}")
            return {'has_update': False, 'latest_version': self.current_version, 'current_version': self.current_version, 'changelog': [], 'error': str(e)}
    
    def download_update(self, version):
        try:
            self._report_progress('downloading', '正在连接更新服务器...', 0)
            download_url = f"{self.config['update_api']}?ver={version}"
            temp_dir = self.base_dir / "data" / "temp_update"
            temp_dir.mkdir(exist_ok=True)
            zip_file = temp_dir / f"{version}.zip"
            self.logger.info(f"正在下载更新包: {version}")
            self._report_progress('downloading', f'正在下载版本 {version}...', 5)
            response = requests.get(download_url, stream=True, timeout=60)
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            self._report_progress('downloading', f'下载中... (0%)', 10)
            with open(zip_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            progress = (downloaded / total_size) * 100
                            actual_progress = 10 + (progress * 0.3)
                            if downloaded % (128 * 1024) == 0:
                                size_mb = downloaded / (1024 * 1024)
                                total_mb = total_size / (1024 * 1024)
                                self._report_progress('downloading', f'下载中... ({size_mb:.1f}/{total_mb:.1f} MB)', int(actual_progress))
                                self.logger.info(f"下载进度: {progress:.1f}%")
            self._report_progress('downloading', '下载完成', 40)
            self.logger.info(f"下载完成: {zip_file}")
            return str(zip_file)
        except Exception as e:
            self.logger.error(f"下载更新包失败: {e}")
            return None
    
    def backup_current_version(self):
        try:
            if not self.config.get('backup_enabled', True):
                return None
            backup_dir = self.base_dir / self.config.get('backup_dir', 'data/backup')
            backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_file = backup_dir / f"backup_{self.current_version}_{timestamp}.zip"
            self.logger.info(f"正在备份到: {backup_file}")
            with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(self.base_dir):
                    dirs[:] = [d for d in dirs if not self._should_skip_backup(os.path.join(root, d))]
                    for file in files:
                        file_path = os.path.join(root, file)
                        if not self._should_skip_backup(file_path):
                            zipf.write(file_path, os.path.relpath(file_path, self.base_dir))
            self.logger.info(f"备份完成: {backup_file}")
            return str(backup_file)
        except Exception as e:
            self.logger.error(f"备份失败: {e}")
            return None
    
    def _should_skip_backup(self, path):
        rel_path = os.path.relpath(path, self.base_dir)
        return (rel_path.startswith('plugins') or rel_path.startswith('data/backup') or rel_path.startswith('data\\backup') or 
                rel_path.startswith('data/temp_update') or rel_path.startswith('data\\temp_update') or '.git' in rel_path or '__pycache__' in rel_path)
    
    def _should_skip_file(self, file_path):
        rel_path = file_path.replace('\\', '/')
        skip_patterns = self.config.get('skip_files', [])
        for pattern in skip_patterns:
            pattern = pattern.replace('\\', '/')
            if (rel_path == pattern.rstrip('/') or (pattern.endswith('/') and rel_path.startswith(pattern)) or 
                fnmatch.fnmatch(rel_path, pattern) or ('/' in pattern and rel_path.startswith(pattern.rstrip('/') + '/'))):
                return True
        return False
    
    def apply_update(self, zip_file, version):
        result = {'success': False, 'message': '', 'skipped_files': [], 'updated_files': [], 'backup_file': None}
        try:
            self._report_progress('backing_up', '正在备份当前版本...', 40)
            backup_file = self.backup_current_version()
            result['backup_file'] = backup_file
            if not backup_file and self.config.get('backup_enabled', True):
                self._report_progress('failed', '备份失败，更新已取消', 0)
                result['message'] = '备份失败，更新已取消'
                return result
            self._report_progress('backing_up', '备份完成', 50)
            self._report_progress('updating', '正在解压更新包...', 55)
            temp_extract = self.base_dir / "data" / "temp_extract"
            if temp_extract.exists():
                shutil.rmtree(temp_extract)
            temp_extract.mkdir(parents=True)
            with zipfile.ZipFile(zip_file, 'r') as zipf:
                zipf.extractall(temp_extract)
            self._report_progress('updating', '解压完成，开始应用更新...', 60)
            total_files = sum(len(files) for _, _, files in os.walk(temp_extract))
            processed_files = 0
            for root, dirs, files in os.walk(temp_extract):
                for file in files:
                    processed_files += 1
                    update_progress = 60 + int((processed_files / max(total_files, 1)) * 30)
                    src_file = os.path.join(root, file)
                    rel_path = os.path.relpath(src_file, temp_extract)
                    dst_file = self.base_dir / rel_path
                    if self._should_skip_file(rel_path):
                        result['skipped_files'].append(rel_path)
                        continue
                    if rel_path == 'main-first.py':
                        try:
                            (self.base_dir / 'main.py').parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src_file, self.base_dir / 'main.py')
                            result['updated_files'].append('main.py (from main-first.py)')
                        except Exception as e:
                            self.logger.error(f"更新 main.py 失败: {e}")
                        continue
                    if rel_path == 'main.py':
                        result['skipped_files'].append(rel_path)
                        continue
                    try:
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dst_file)
                        result['updated_files'].append(rel_path)
                        if len(result['updated_files']) % 50 == 0:
                            self._report_progress('updating', f'正在更新文件... ({len(result["updated_files"])}/{total_files})', update_progress)
                    except Exception as e:
                        self.logger.error(f"复制文件失败 {rel_path}: {e}")
            self._report_progress('updating', '文件更新完成', 90)
            self._report_progress('updating', '正在清理临时文件...', 95)
            try:
                shutil.rmtree(temp_extract)
                os.remove(zip_file)
            except:
                pass
            result['success'] = True
            result['message'] = f'更新成功！已更新 {len(result["updated_files"])} 个文件，跳过 {len(result["skipped_files"])} 个文件'
            self._report_progress('completed', result['message'], 100)
            self.logger.info(f"更新完成: {result['message']}")
        except Exception as e:
            result['message'] = f'更新失败: {str(e)}'
            self._report_progress('failed', result['message'], 0)
            self.logger.error(f"应用更新失败: {e}")
        return result
    
    def update_to_version(self, version):
        self.logger.info(f"开始更新到版本: {version}")
        self._report_progress('preparing', f'准备更新到版本 {version}...', 0)
        self._save_version(version)
        zip_file = self.download_update(version)
        if not zip_file:
            return {'success': False, 'message': '下载更新包失败'}
        return self.apply_update(zip_file, version)
    
    def update_to_latest(self):
        check_result = self.check_for_updates()
        if check_result.get('error'):
            return {'success': False, 'message': f"检查更新失败: {check_result['error']}"}
        if not check_result['has_update']:
            return {'success': False, 'message': '当前已是最新版本', 'current_version': self.current_version}
        return self.update_to_version(check_result['latest_version'])
    
    def start_auto_check(self):
        if not self.config.get('enabled', False):
            return False
        if self._update_thread and self._update_thread.is_alive():
            return False
        self._stop_event.clear()
        self._update_thread = threading.Thread(target=self._auto_check_loop, daemon=True)
        self._update_thread.start()
        self.logger.info(f"自动更新检查已启动，间隔: {self.config['check_interval']}秒")
        return True
    
    def stop_auto_check(self):
        if self._update_thread and self._update_thread.is_alive():
            self._stop_event.set()
            return True
        return False
    
    def _auto_check_loop(self):
        interval = self.config.get('check_interval', 1800)
        while not self._stop_event.is_set():
            try:
                check_result = self.check_for_updates()
                if check_result['has_update']:
                    self.logger.info(f"发现新版本: {check_result['latest_version']}")
                    if self.config.get('auto_update', False):
                        result = self.update_to_latest()
                        if result['success']:
                            self.logger.info("自动更新成功！框架将在下次重启时应用更新")
                        else:
                            self.logger.error(f"自动更新失败: {result['message']}")
                    else:
                        self.logger.info("检测到新版本，但自动更新未启用")
            except Exception as e:
                self.logger.error(f"自动检查更新出错: {e}")
            self._stop_event.wait(interval)

_updater_instance = None

def get_updater():
    global _updater_instance
    if _updater_instance is None:
        _updater_instance = FrameworkUpdater()
    return _updater_instance

