#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os, json, shutil, zipfile, logging, requests, fnmatch
from datetime import datetime
from pathlib import Path

GITHUB_REPO = "lengxi-root/ElainaBot"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}"

DOWNLOAD_SOURCES = {
    'proxy': {
        'name': '代理服务器 (推荐)',
        'description': '通过 i.elaina.vin 代理下载，国内访问更稳定',
        'api_url': 'https://i.elaina.vin/api/elainabot/',
        'download_url': 'https://i.elaina.vin/api/elainabot/?ver={version}',
    },
    'github': {
        'name': 'GitHub 直连',
        'description': '直接从 GitHub 下载，需要能访问 GitHub',
        'api_url': f'{GITHUB_API_URL}/commits',
        'download_url': f'https://codeload.github.com/{GITHUB_REPO}/zip/refs/heads/main',
        'always_latest': True,
    },
    'ghfast': {
        'name': 'GitHub 加速 (ghfast.top)',
        'description': '通过 ghfast.top 加速下载',
        'api_url': 'https://i.elaina.vin/api/elainabot/',
        'download_url': f'https://ghfast.top/https://github.com/{GITHUB_REPO}/archive/main.zip',
        'always_latest': True,
    },
    'github-sha': {
        'name': 'GitHub 直连 (按版本)',
        'description': '按 commit SHA 下载指定版本，可回退历史版本',
        'api_url': f'{GITHUB_API_URL}/commits',
        'download_url': f'https://codeload.github.com/{GITHUB_REPO}/zip/{{version}}',
    },
}

class FrameworkUpdater:
    def __init__(self):
        self.base_dir = Path(__file__).parent.parent.parent.absolute()
        self.version_file = self.base_dir / "data" / "version.json"
        self.settings_file = self.base_dir / "data" / "update_settings.json"
        (self.base_dir / "data").mkdir(exist_ok=True)
        
        self.config = self._load_config()
        self.current_version = self._load_version()
        self.download_source = self._load_setting('download_source', 'proxy')
        self.current_progress = {'stage': 'idle', 'message': '', 'progress': 0, 'is_updating': False}
    
    def _load_config(self):
        try:
            from config import PROTECTED_FILES
            return {'skip_files': PROTECTED_FILES, 'backup_enabled': True, 'backup_dir': 'data/backup'}
        except:
            return {'skip_files': ['config.py', 'data/', 'plugins/'], 'backup_enabled': True, 'backup_dir': 'data/backup'}
    
    def _load_version(self):
        try:
            return json.load(open(self.version_file, encoding='utf-8')).get('version', 'unknown')
        except:
            return 'unknown'
    
    def _save_version(self, version):
        try:
            json.dump({'version': version, 'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}, 
                     open(self.version_file, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
            self.current_version = version
        except:
            pass
    
    def _load_setting(self, key, default):
        try:
            return json.load(open(self.settings_file, encoding='utf-8')).get(key, default)
        except:
            return default
    
    def _save_setting(self, key, value):
        try:
            data = json.load(open(self.settings_file, encoding='utf-8')) if self.settings_file.exists() else {}
            data[key] = value
            json.dump(data, open(self.settings_file, 'w', encoding='utf-8'), indent=2)
        except:
            pass
    
    def _report_progress(self, stage, message, progress=0):
        self.current_progress = {'stage': stage, 'message': message, 'progress': progress, 
                                  'is_updating': stage not in ('idle', 'completed', 'failed')}
    
    def get_progress(self):
        return self.current_progress.copy()
    
    def set_download_source(self, source):
        if source in DOWNLOAD_SOURCES:
            self.download_source = source
            self._save_setting('download_source', source)
            return True
        return False
    
    def get_available_sources(self):
        return {k: {'name': v['name'], 'description': v['description']} for k, v in DOWNLOAD_SOURCES.items()}
    
    def get_version_info(self):
        try:
            return json.load(open(self.version_file, encoding='utf-8'))
        except:
            return {'version': self.current_version, 'update_time': 'unknown'}
    
    def test_source_connection(self, source=None):
        source = source or self.download_source
        if source not in DOWNLOAD_SOURCES:
            return {'success': False, 'message': '无效的下载源'}
        try:
            start = datetime.now()
            resp = requests.get(DOWNLOAD_SOURCES[source]['api_url'], timeout=10)
            elapsed = (datetime.now() - start).total_seconds()
            if resp.status_code == 200:
                return {'success': True, 'message': f'连接成功，响应时间: {elapsed:.2f}秒', 'latency': elapsed}
            return {'success': False, 'message': f'服务器返回错误: {resp.status_code}'}
        except requests.exceptions.Timeout:
            return {'success': False, 'message': '连接超时'}
        except Exception as e:
            return {'success': False, 'message': f'连接失败: {e}'}
    
    def check_for_updates(self):
        source_config = DOWNLOAD_SOURCES.get(self.download_source, DOWNLOAD_SOURCES['proxy'])
        try:
            self._report_progress('checking', f'正在检查更新...', 0)
            resp = requests.get(source_config['api_url'], timeout=15)
            commits = resp.json()
            if not commits:
                return {'has_update': False, 'error': '无法获取更新信息'}
            
            latest = commits[0].get('sha', '')[:8]
            current = self.current_version[:8] if len(self.current_version) >= 8 else self.current_version
            has_update = (current != latest and self.current_version != 'unknown') or self.current_version == 'unknown'
            
            return {'has_update': has_update, 'latest_version': latest, 'current_version': self.current_version,
                    'changelog': commits[:10], 'error': None, 'source': self.download_source}
        except Exception as e:
            return {'has_update': False, 'error': str(e), 'source': self.download_source}
    
    def download_update(self, version):
        source_config = DOWNLOAD_SOURCES.get(self.download_source, DOWNLOAD_SOURCES['proxy'])
        try:
            self._report_progress('downloading', '正在下载...', 5)
            temp_dir = self.base_dir / "data" / "temp_update"
            temp_dir.mkdir(exist_ok=True)
            zip_file = temp_dir / f"{version}.zip"
            
            url = source_config['download_url'] if source_config.get('always_latest') else source_config['download_url'].format(version=version)
            resp = requests.get(url, stream=True, timeout=120)
            resp.raise_for_status()
            
            total = int(resp.headers.get('content-length', 0))
            downloaded = 0
            with open(zip_file, 'wb') as f:
                for chunk in resp.iter_content(8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            self._report_progress('downloading', f'下载中... {downloaded*100//total}%', 10 + downloaded*30//total)
            
            self._report_progress('downloading', '下载完成', 40)
            return str(zip_file)
        except Exception as e:
            self._report_progress('failed', f'下载失败: {e}', 0)
            return None
    
    def backup_current_version(self):
        if not self.config.get('backup_enabled'):
            return 'disabled'
        try:
            backup_dir = self.base_dir / self.config['backup_dir']
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_file = backup_dir / f"backup_{self.current_version}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            
            skip = ('plugins', 'data/backup', 'data/temp', '.git', '__pycache__')
            with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(self.base_dir):
                    rel = os.path.relpath(root, self.base_dir)
                    if any(s in rel for s in skip):
                        dirs[:] = []
                        continue
                    for f in files:
                        fp = os.path.join(root, f)
                        zf.write(fp, os.path.relpath(fp, self.base_dir))
            return str(backup_file)
        except:
            return None
    
    def _should_skip(self, path):
        path = path.replace('\\', '/')
        for p in self.config.get('skip_files', []):
            p = p.replace('\\', '/')
            if path == p.rstrip('/') or path.startswith(p.rstrip('/') + '/') or fnmatch.fnmatch(path, p):
                return True
        return False
    
    def apply_update(self, zip_file, version):
        result = {'success': False, 'message': '', 'updated': 0, 'skipped': 0}
        try:
            self._report_progress('backing_up', '正在备份...', 45)
            backup = self.backup_current_version()
            if not backup and self.config.get('backup_enabled'):
                self._report_progress('failed', '备份失败', 0)
                return result
            
            self._report_progress('updating', '正在解压...', 55)
            temp = self.base_dir / "data" / "temp_extract"
            if temp.exists():
                shutil.rmtree(temp)
            temp.mkdir(parents=True)
            
            with zipfile.ZipFile(zip_file, 'r') as zf:
                zf.extractall(temp)
            
            # 处理 GitHub 格式（有根目录）
            items = list(temp.iterdir())
            source = items[0] if len(items) == 1 and items[0].is_dir() else temp
            
            self._report_progress('updating', '正在更新文件...', 60)
            for root, _, files in os.walk(source):
                for f in files:
                    src = os.path.join(root, f)
                    rel = os.path.relpath(src, source)
                    if self._should_skip(rel):
                        result['skipped'] += 1
                        continue
                    dst = self.base_dir / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    result['updated'] += 1
            
            shutil.rmtree(temp, ignore_errors=True)
            os.remove(zip_file) if os.path.exists(zip_file) else None
            
            result['success'] = True
            result['message'] = f'更新成功！更新 {result["updated"]} 个文件，跳过 {result["skipped"]} 个'
            self._report_progress('completed', result['message'], 100)
        except Exception as e:
            result['message'] = f'更新失败: {e}'
            self._report_progress('failed', result['message'], 0)
        return result
    
    def update_to_version(self, version):
        self._report_progress('preparing', f'准备更新到 {version}...', 0)
        zip_file = self.download_update(version)
        if not zip_file:
            return {'success': False, 'message': '下载失败'}
        result = self.apply_update(zip_file, version)
        if result['success']:
            self._save_version(version)
        return result
    
    def update_to_latest(self):
        check = self.check_for_updates()
        if check.get('error'):
            return {'success': False, 'message': f"检查失败: {check['error']}"}
        if not check['has_update']:
            return {'success': False, 'message': '已是最新版本'}
        return self.update_to_version(check['latest_version'])
    
    def force_update(self):
        """强制更新到最新版本，不检查是否已是最新"""
        source_config = DOWNLOAD_SOURCES.get(self.download_source, DOWNLOAD_SOURCES['proxy'])
        try:
            self._report_progress('checking', '获取最新版本...', 0)
            resp = requests.get(source_config['api_url'], timeout=15)
            commits = resp.json()
            if not commits:
                return {'success': False, 'message': '无法获取版本信息'}
            latest = commits[0].get('sha', '')[:8]
            return self.update_to_version(latest)
        except Exception as e:
            self._report_progress('failed', f'获取版本失败: {e}', 0)
            return {'success': False, 'message': str(e)}

_updater = None
def get_updater():
    global _updater
    if not _updater:
        _updater = FrameworkUpdater()
    return _updater
