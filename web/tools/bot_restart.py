#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, json, platform, subprocess, psutil, importlib.util, time, threading
from datetime import datetime


def execute_bot_restart(restart_status=None):

    current_pid = os.getpid()
    current_dir = os.getcwd()
    main_py_path = os.path.join(current_dir, 'main.py')
    
    if not os.path.exists(main_py_path):
        return {'success': False, 'error': 'main.py文件不存在！'}
    
    config_path = os.path.join(current_dir, 'config.py')
    config = None
    if os.path.exists(config_path):
        try:
            spec = importlib.util.spec_from_file_location("config", config_path)
            config = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(config)
        except Exception as e:
            pass
    
    is_dual_process = False
    main_port = 5001
    web_port = 5002
    
    if config and hasattr(config, 'SERVER_CONFIG'):
        server_config = config.SERVER_CONFIG
        is_dual_process = server_config.get('web_dual_process', False)
        main_port = server_config.get('port', 5001)
        web_port = server_config.get('web_port', 5002)
    
    restart_mode = "独立进程模式" if is_dual_process else "单进程模式"
    
    def _get_restart_status_file():
        plugin_dir = os.path.join(current_dir, 'plugins', 'system')
        data_dir = os.path.join(plugin_dir, 'data')
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
        return os.path.join(data_dir, 'restart_status.json')
    
    if restart_status is None:
        restart_status = {
            'restart_time': datetime.now().isoformat(),
            'completed': False,
            'message_id': None,
            'user_id': 'web_admin',
            'group_id': 'web_panel'
        }
    
    restart_status_file = _get_restart_status_file()
    with open(restart_status_file, 'w', encoding='utf-8') as f:
        json.dump(restart_status, f, ensure_ascii=False)
    
    def _find_processes_by_port(port):
        pids = []
        try:
            for conn in psutil.net_connections():
                if conn.laddr.port == port and conn.status == 'LISTEN':
                    try:
                        proc = psutil.Process(conn.pid)
                        pids.append(conn.pid)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
        except Exception as e:
            pass
        return pids
    
    def _create_restart_python_script(main_py_path, is_dual_process=False, main_port=5001, web_port=5002):
        current_python_pid = current_pid
        is_windows = platform.system().lower() == 'windows'
        
        if is_windows:
            script_content = f'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import subprocess

def main():
    main_py_path = r"{main_py_path}"
    
    try:
        print("等待3秒后启动新进程...")
        time.sleep(3)
        
        os.chdir(os.path.dirname(main_py_path))
        print(f"正在重新启动主程序: {{main_py_path}}")
        
        subprocess.Popen(
            [sys.executable, main_py_path],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            cwd=os.path.dirname(main_py_path)
        )
        
        print("重启命令已执行")
        time.sleep(1)
        
        try:
            script_path = __file__
            if os.path.exists(script_path):
                os.remove(script_path)
        except:
            pass
        sys.exit(0)
        
    except Exception as e:
        print(f"重启失败: {{e}}")
        sys.exit(1)

if __name__ == "__main__":
    main()
'''
        else:
            # Linux/Unix 重启脚本
            if is_dual_process:
                kill_ports_code = f"""
        # 独立进程模式：查找并杀死主程序和web面板进程
        ports_to_kill = [{main_port}, {web_port}]
        pids_to_kill = []
        
        for port in ports_to_kill:
            for conn in psutil.net_connections():
                if conn.laddr.port == port and conn.status == 'LISTEN':
                    try:
                        proc = psutil.Process(conn.pid)
                        pids_to_kill.append(conn.pid)
                        print(f"找到端口{{port}}的进程: PID {{conn.pid}}")
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            
            pids_to_kill = list(set(pids_to_kill))
            
            for pid in pids_to_kill:
            try:
                proc = psutil.Process(pid)
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                    print(f"Linux: 进程 PID {{pid}} 已正常终止")
                except psutil.TimeoutExpired:
                    proc.kill()
                    print(f"Linux: 强制杀死进程 PID {{pid}}")
            except Exception as e:
                print(f"杀死进程{{pid}}失败: {{e}}")
        
        # 等待进程完全终止
        time.sleep(1)
"""
            else:
                kill_ports_code = f"""
        # 单进程模式：杀死指定的Python进程
        target_pid = {current_python_pid}
        try:
            proc = psutil.Process(target_pid)
            print(f"准备杀死Python进程: PID {{target_pid}}")
            
            proc.terminate()
            try:
                proc.wait(timeout=3)
                print(f"Linux: 进程 PID {{target_pid}} 已正常终止")
            except psutil.TimeoutExpired:
                proc.kill()
                print(f"Linux: 强制杀死进程 PID {{target_pid}}")
        except psutil.NoSuchProcess:
            print(f"进程 {{target_pid}} 不存在或已终止")
        except Exception as e:
            print(f"杀死进程{{target_pid}}失败: {{e}}")
        
        # 等待进程完全终止
        time.sleep(1)
"""
            
            script_content = f'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import signal
import platform
import subprocess
import psutil

def main():
    main_py_path = r"{main_py_path}"
    
    try:{kill_ports_code}
    except Exception as e:
        print(f"杀死进程过程中出错: {{e}}")
    
    time.sleep(1)
    
    ports_to_check = [{main_port}, {web_port}] if {is_dual_process} else [{main_port}]
    max_wait = 5
    wait_count = 0
    while wait_count < max_wait:
        ports_still_occupied = False
        try:
            for conn in psutil.net_connections():
                if conn.laddr.port in ports_to_check and conn.status == 'LISTEN':
                    ports_still_occupied = True
                    print(f"端口{{conn.laddr.port}}仍被PID{{conn.pid}}占用")
                    break
        except:
            pass
            
        if not ports_still_occupied:
            print("确认端口已释放，可以启动新进程")
            break
        else:
            print(f"端口仍被占用，继续等待... ({{wait_count + 1}}/{{max_wait}})")
            time.sleep(1)
            wait_count += 1
    
    try:
        os.chdir(os.path.dirname(main_py_path))
        
        print(f"正在重新启动主程序: {{main_py_path}}")
        
        try:
            script_path = __file__
            if os.path.exists(script_path):
                os.remove(script_path)
        except:
            pass
        os.execv(sys.executable, [sys.executable, main_py_path])
        
        print("重启命令已执行")
        
    except Exception as e:
        print(f"重启失败: {{e}}")
        sys.exit(1)

if __name__ == "__main__":
    main()
'''
        
        return script_content
    
    try:
        restart_script_content = _create_restart_python_script(
            main_py_path, is_dual_process, main_port, web_port
        )
        restart_script_path = os.path.join(current_dir, 'bot_restarter.py')
        
        with open(restart_script_path, 'w', encoding='utf-8') as f:
            f.write(restart_script_content)
        
        if is_dual_process:
            try:
                main_pids = _find_processes_by_port(main_port)
                web_pids = _find_processes_by_port(web_port)
            except Exception as e:
                pass
        
        is_windows = platform.system().lower() == 'windows'
        
        if is_windows:
            subprocess.Popen([sys.executable, restart_script_path], cwd=current_dir,
                           creationflags=subprocess.CREATE_NEW_CONSOLE)
            
            def delayed_exit():
                time.sleep(1)
                os._exit(0)
            
            threading.Thread(target=delayed_exit, daemon=True).start()
            return {
                'success': True,
                'message': f'🔄 正在重启机器人... ({restart_mode})\n⏱️ 主进程将在1秒后退出，新进程将在3秒后启动'
            }
        else:
            subprocess.Popen([sys.executable, restart_script_path], cwd=current_dir,
                           start_new_session=True)
            return {
                'success': True,
                'message': f'🔄 正在重启机器人... ({restart_mode})\n⏱️ 预计重启时间: 1秒'
            }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

