#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os, sys, json, shutil, zipfile, logging, requests, fnmatch
from datetime import datetime
from pathlib import Path

class FrameworkUpdater:
    __slots__ = ('base_dir', 'version_file', 'config', 'logger', 'current_version', 'progress_callback', 'current_progress')
    
    def __init__(self, config=None):
        self.base_dir = Path(__file__).parent.parent.parent.absolute()
        self.version_file = self.base_dir / "data" / "version.json"
        self.config = config or self._load_config()
        self.logger = logging.getLogger('FrameworkUpdater')
        (self.base_dir / "data").mkdir(exist_ok=True)
        self.current_version = self._load_version()
        self.progress_callback = None
        self.current_progress = {'stage': 'idle', 'message': '未开始', 'progress': 0, 'is_updating': False}
    
    def _report_progress(self, stage, message, progress=0):
        self.current_progress = {'stage': stage, 'message': message, 'progress': progress, 'is_updating': stage not in ('idle', 'completed', 'failed')}
        if self.progress_callback:
            try:
                self.progress_callback(self.current_progress)
            except:
                pass
    
    def get_progress(self):
        return self.current_progress.copy()
    
    def _load_config(self):
        try:
            from config import PROTECTED_FILES
            config = {'skip_files': PROTECTED_FILES, 'backup_enabled': True}
        except:
            config = {'backup_enabled': True, 'skip_files': ["config.py", "data/", "plugins/"]}
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
            with open(self.version_file, 'w', encoding='utf-8') as f:
                json.dump({'version': version, 'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'updated_from': self.current_version}, f, indent=2, ensure_ascii=False)
            self.current_version = version
            return True
        except:
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
            commits = requests.get(self.config['update_api'], timeout=10).json()
            if not commits:
                return {'has_update': False, 'latest_version': self.current_version, 'current_version': self.current_version, 'changelog': [], 'error': '无法获取更新信息'}
            latest = commits[0].get('sha', '')[:7]
            has_update = (self.current_version != latest and self.current_version != 'unknown') or self.current_version == 'unknown'
            return {'has_update': has_update, 'latest_version': latest, 'current_version': self.current_version, 'changelog': commits[:10], 'error': None}
        except Exception as e:
            return {'has_update': False, 'latest_version': self.current_version, 'current_version': self.current_version, 'changelog': [], 'error': str(e)}
    
    def download_update(self, version):
        try:
            self._report_progress('downloading', '正在连接更新服务器...', 0)
            temp_dir = self.base_dir / "data" / "temp_update"
            temp_dir.mkdir(exist_ok=True)
            zip_file = temp_dir / f"{version}.zip"
            
            self._report_progress('downloading', f'正在下载版本 {version}...', 5)
            resp = requests.get(f"{self.config['update_api']}?ver={version}", stream=True, timeout=60)
            resp.raise_for_status()
            
            total = int(resp.headers.get('content-length', 0))
            downloaded = 0
            with open(zip_file, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0 and downloaded % (128 * 1024) == 0:
                            self._report_progress('downloading', f'下载中... ({downloaded/(1024*1024):.1f}/{total/(1024*1024):.1f} MB)', int(10 + (downloaded/total)*30))
            
            self._report_progress('downloading', '下载完成', 40)
            return str(zip_file)
        except:
            return None
    
    def backup_current_version(self):
        if not self.config.get('backup_enabled', True):
            return None
        try:
            backup_dir = self.base_dir / self.config.get('backup_dir', 'data/backup')
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_file = backup_dir / f"backup_{self.current_version}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            
            skip_prefixes = ('plugins', 'data/backup', 'data\\backup', 'data/temp_update', 'data\\temp_update')
            skip_contains = ('.git', '__pycache__')
            
            with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(self.base_dir):
                    rel = os.path.relpath(root, self.base_dir)
                    if any(rel.startswith(p) for p in skip_prefixes) or any(s in rel for s in skip_contains):
                        dirs[:] = []
                        continue
                    dirs[:] = [d for d in dirs if not any(os.path.relpath(os.path.join(root, d), self.base_dir).startswith(p) for p in skip_prefixes)]
                    for f in files:
                        fp = os.path.join(root, f)
                        if not any(s in fp for s in skip_contains):
                            zf.write(fp, os.path.relpath(fp, self.base_dir))
            return str(backup_file)
        except:
            return None
    
    def _should_skip_file(self, file_path):
        rel = file_path.replace('\\', '/')
        for pattern in self.config.get('skip_files', []):
            p = pattern.replace('\\', '/')
            if rel == p.rstrip('/') or (p.endswith('/') and rel.startswith(p)) or fnmatch.fnmatch(rel, p) or ('/' in p and rel.startswith(p.rstrip('/') + '/')):
                return True
        return False
    
    def apply_update(self, zip_file, version):
        result = {'success': False, 'message': '', 'skipped_files': [], 'updated_files': [], 'backup_file': None}
        try:
            self._report_progress('backing_up', '正在备份当前版本...', 40)
            result['backup_file'] = self.backup_current_version()
            if not result['backup_file'] and self.config.get('backup_enabled', True):
                self._report_progress('failed', '备份失败，更新已取消', 0)
                result['message'] = '备份失败，更新已取消'
                return result
            
            self._report_progress('updating', '正在解压更新包...', 55)
            temp_extract = self.base_dir / "data" / "temp_extract"
            if temp_extract.exists():
                shutil.rmtree(temp_extract)
            temp_extract.mkdir(parents=True)
            
            with zipfile.ZipFile(zip_file, 'r') as zf:
                zf.extractall(temp_extract)
            
            self._report_progress('updating', '解压完成，开始应用更新...', 60)
            total = sum(len(files) for _, _, files in os.walk(temp_extract))
            processed = 0
            
            for root, _, files in os.walk(temp_extract):
                for f in files:
                    processed += 1
                    src = os.path.join(root, f)
                    rel = os.path.relpath(src, temp_extract)
                    
                    if self._should_skip_file(rel):
                        result['skipped_files'].append(rel)
                        continue
                    
                    try:
                        dst = self.base_dir / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)
                        result['updated_files'].append(rel)
                        if len(result['updated_files']) % 50 == 0:
                            self._report_progress('updating', f'正在更新文件... ({len(result["updated_files"])}/{total})', 60 + int((processed/max(total,1))*30))
                    except:
                        pass
            
            self._report_progress('updating', '正在清理临时文件...', 95)
            try:
                shutil.rmtree(temp_extract)
                os.remove(zip_file)
            except:
                pass
            
            result['success'] = True
            result['message'] = f'更新成功！已更新 {len(result["updated_files"])} 个文件，跳过 {len(result["skipped_files"])} 个文件'
            self._report_progress('completed', result['message'], 100)
        except Exception as e:
            result['message'] = f'更新失败: {e}'
            self._report_progress('failed', result['message'], 0)
        return result
    
    def update_to_version(self, version):
        self._report_progress('preparing', f'准备更新到版本 {version}...', 0)
        self._save_version(version)
        zip_file = self.download_update(version)
        if not zip_file:
            return {'success': False, 'message': '下载更新包失败'}
        return self.apply_update(zip_file, version)
    
    def update_to_latest(self):
        check = self.check_for_updates()
        if check.get('error'):
            return {'success': False, 'message': f"检查更新失败: {check['error']}"}
        if not check['has_update']:
            return {'success': False, 'message': '当前已是最新版本', 'current_version': self.current_version}
        return self.update_to_version(check['latest_version'])

_updater_instance = None

def get_updater():
    global _updater_instance
    if _updater_instance is None:
        _updater_instance = FrameworkUpdater()
    return _updater_instance
