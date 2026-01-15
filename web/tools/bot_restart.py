#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, json, platform, subprocess, psutil, importlib.util, time, threading
from datetime import datetime

_CURRENT_DIR = os.getcwd()
_MAIN_PY_PATH = os.path.join(_CURRENT_DIR, 'main.py')
_CONFIG_PATH = os.path.join(_CURRENT_DIR, 'config.py')
_RESTART_STATUS_DIR = os.path.join(_CURRENT_DIR, 'plugins', 'system', 'data')
_RESTART_STATUS_FILE = os.path.join(_RESTART_STATUS_DIR, 'restart_status.json')
_RESTARTER_SCRIPT_PATH = os.path.join(_CURRENT_DIR, 'bot_restarter.py')
_IS_WINDOWS = platform.system().lower() == 'windows'
_LISTEN_STATUS = 'LISTEN'
_DEFAULT_PORT = 5001

_WIN_RESTART_TEMPLATE = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, time, subprocess

def main():
    time.sleep(3)
    main_path = r"{main_py_path}"
    os.chdir(os.path.dirname(main_path))
    subprocess.Popen([sys.executable, main_path], creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=os.path.dirname(main_path))
    time.sleep(1)
    try:
        os.remove(__file__)
    except: pass
    sys.exit(0)

if __name__ == "__main__":
    main()
'''

_UNIX_RESTART_TEMPLATE = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, time, psutil

def main():
    main_path = r"{main_py_path}"
    port = {main_port}
    
    try:
        for conn in psutil.net_connections():
            if conn.laddr.port == port and conn.status == 'LISTEN':
                try:
                    proc = psutil.Process(conn.pid)
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
    except: pass
    
    time.sleep(1)
    for _ in range(5):
        occupied = False
        try:
            for conn in psutil.net_connections():
                if conn.laddr.port == port and conn.status == 'LISTEN':
                    occupied = True
                    break
        except: pass
        if not occupied:
            break
        time.sleep(1)
    
    try:
        os.chdir(os.path.dirname(main_path))
        try:
            os.remove(__file__)
        except: pass
        os.execv(sys.executable, [sys.executable, main_path])
    except Exception as e:
        sys.exit(1)

if __name__ == "__main__":
    main()
'''


def _load_config_port():
    if not os.path.exists(_CONFIG_PATH):
        return _DEFAULT_PORT
    try:
        spec = importlib.util.spec_from_file_location("config", _CONFIG_PATH)
        config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config)
        if hasattr(config, 'SERVER_CONFIG'):
            server_config = config.SERVER_CONFIG
            # 如果启用了SSL自动证书，检查证书是否存在
            if server_config.get('ssl_auto_cert'):
                cert_path = os.path.join(_CURRENT_DIR, 'data', 'ssl', 'cert.pem')
                if os.path.exists(cert_path):
                    return server_config.get('ssl_port', 8443)
            # 如果启用了SSL（手动模式）
            elif server_config.get('ssl_enabled'):
                return server_config.get('ssl_port', 8443)
            return server_config.get('port', _DEFAULT_PORT)
    except: pass
    return _DEFAULT_PORT


def _ensure_status_dir():
    if not os.path.exists(_RESTART_STATUS_DIR):
        os.makedirs(_RESTART_STATUS_DIR)


def execute_bot_restart(restart_status=None):
    if not os.path.exists(_MAIN_PY_PATH):
        return {'success': False, 'error': 'main.py文件不存在！'}
    
    main_port = _load_config_port()
    
    if restart_status is None:
        restart_status = {
            'restart_time': datetime.now().isoformat(),
            'completed': False,
            'message_id': None,
            'user_id': 'web_admin',
            'group_id': 'web_panel'
        }
    
    _ensure_status_dir()
    with open(_RESTART_STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(restart_status, f, ensure_ascii=False)
    
    try:
        if _IS_WINDOWS:
            script_content = _WIN_RESTART_TEMPLATE.format(main_py_path=_MAIN_PY_PATH)
        else:
            script_content = _UNIX_RESTART_TEMPLATE.format(main_py_path=_MAIN_PY_PATH, main_port=main_port)
        
        with open(_RESTARTER_SCRIPT_PATH, 'w', encoding='utf-8') as f:
            f.write(script_content)
        
        if _IS_WINDOWS:
            subprocess.Popen([sys.executable, _RESTARTER_SCRIPT_PATH], cwd=_CURRENT_DIR, creationflags=subprocess.CREATE_NEW_CONSOLE)
            threading.Thread(target=lambda: (time.sleep(1), os._exit(0)), daemon=True).start()
            return {'success': True, 'message': '🔄 正在重启机器人... (单进程模式)\n⏱️ 主进程将在1秒后退出，新进程将在3秒后启动'}
        else:
            subprocess.Popen([sys.executable, _RESTARTER_SCRIPT_PATH], cwd=_CURRENT_DIR, start_new_session=True)
            return {'success': True, 'message': '🔄 正在重启机器人... (单进程模式)\n⏱️ 预计重启时间: 1秒'}
    except Exception as e:
        return {'success': False, 'error': str(e)}
