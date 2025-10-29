import os, gc, time, psutil
from datetime import datetime, timedelta

START_TIME = datetime.now()
_last_gc_time = 0
_gc_interval = 30
_last_gc_log_time = 0

add_error_log = None

def set_start_time(start_time):
    global START_TIME
    START_TIME = start_time

def set_error_log_func(func):
    global add_error_log
    add_error_log = func

def get_websocket_status():
    try:
        from function.ws_client import get_client
        client = get_client("qq_bot")
        return "连接成功" if (client and hasattr(client, 'connected') and client.connected) else "连接失败"
    except:
        return "连接失败"

def get_cpu_model():
    cpu_model = "未知处理器"
    try:
        import platform
        system_type = platform.system()
        
        if system_type == 'Windows':
            try:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
                cpu_model = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
                winreg.CloseKey(key)
            except:
                pass
        
        elif system_type == 'Linux':
            try:
                with open('/proc/cpuinfo', 'r') as f:
                    for line in f:
                        if 'model name' in line.lower():
                            cpu_model = line.split(':', 1)[1].strip()
                            break
            except:
                pass
    except:
        pass
    
    return cpu_model

def get_disk_info():
    try:
        disk_path = os.path.abspath(os.getcwd())
        disk_usage = psutil.disk_usage(disk_path)
        
        disk_info = {
            'total': float(disk_usage.total),
            'used': float(disk_usage.used),
            'free': float(disk_usage.free),
            'percent': float(disk_usage.percent)
        }
        
        framework_dir_size = 0
        framework_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        for root, dirs, files in os.walk(framework_root):
            for file in files:
                try:
                    file_path = os.path.join(root, file)
                    if os.path.isfile(file_path):
                        framework_dir_size += os.path.getsize(file_path)
                except Exception:
                    pass
        
        disk_info['framework_usage'] = float(framework_dir_size)
        
        return disk_info
    
    except Exception:
        return {
            'total': float(100 * 1024 * 1024 * 1024),
            'used': float(50 * 1024 * 1024 * 1024),
            'free': float(50 * 1024 * 1024 * 1024),
            'percent': float(50.0),
            'framework_usage': float(1 * 1024 * 1024 * 1024)
        }

def get_system_info():
    global _last_gc_time
    
    try:
        process = psutil.Process(os.getpid())
        current_time = time.time()
        collected = 0
        
        if current_time - _last_gc_time >= _gc_interval:
            collected = gc.collect(0)
            _last_gc_time = current_time
        
        memory_info = process.memory_info()
        rss = memory_info.rss / 1024 / 1024
        
        system_memory = psutil.virtual_memory()
        system_memory_total = system_memory.total / (1024 * 1024)
        system_memory_used = system_memory.used / (1024 * 1024)
        system_memory_percent = system_memory.percent
        
        process_memory_used = rss
        
        try:
            cpu_cores = psutil.cpu_count(logical=True)
            
            cpu_percent = process.cpu_percent(interval=0.05)
            system_cpu_percent = psutil.cpu_percent(interval=0.05)
            
            if cpu_percent <= 0:
                cpu_percent = 1.0
            if system_cpu_percent <= 0:
                system_cpu_percent = 5.0
        except Exception as e:
            if add_error_log:
                add_error_log(f"获取CPU信息失败: {str(e)}")
            cpu_cores = 1
            cpu_percent = 1.0
            system_cpu_percent = 5.0
        
        app_uptime_seconds = int((datetime.now() - START_TIME).total_seconds())
        
        try:
            boot_time = datetime.fromtimestamp(psutil.boot_time())
            system_uptime = datetime.now() - boot_time
            system_uptime_seconds = int(system_uptime.total_seconds())
            boot_time_str = boot_time.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            system_uptime_seconds = app_uptime_seconds
            boot_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        try:
            start_time_str = START_TIME.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            start_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        try:
            import platform
            system_version = platform.platform()
        except Exception:
            system_version = "未知"
        
        cpu_model = get_cpu_model()
        
        disk_info = get_disk_info()
        
        system_info = {
            'cpu_percent': float(system_cpu_percent),
            'framework_cpu_percent': float(cpu_percent),
            'cpu_cores': cpu_cores,
            'cpu_model': cpu_model,
            
            'memory_percent': float(system_memory_percent),
            'memory_used': float(system_memory_used),
            'memory_total': float(system_memory_total),
            'total_memory': float(system_memory_total),
            'system_memory_total_bytes': float(system_memory.total),
            'framework_memory_percent': float((rss / system_memory_total) * 100 if system_memory_total > 0 else 5.0),
            'framework_memory_total': float(rss),
            
            'gc_counts': list(gc.get_count()),
            'objects_count': len(gc.get_objects()),
            
            'disk_info': disk_info,
            
            'uptime': app_uptime_seconds,
            'system_uptime': system_uptime_seconds,
            'start_time': start_time_str,
            'boot_time': boot_time_str,
            
            'system_version': system_version
        }
        
        return system_info
    
    except Exception as e:
        if add_error_log:
            add_error_log(f"获取系统信息失败: {str(e)}")
        
        return {
            'cpu_percent': 5.0,
            'framework_cpu_percent': 1.0,
            'cpu_cores': 4,
            'cpu_model': '未知处理器',
            'memory_percent': 50.0,
            'memory_used': 400.0,
            'memory_total': 8192.0,
            'total_memory': 8192.0,
            'system_memory_total_bytes': 8192.0 * 1024 * 1024,
            'framework_memory_percent': 5.0,
            'framework_memory_total': 400.0,
            'gc_counts': [0, 0, 0],
            'objects_count': 1000,
            'disk_info': {
                'total': float(100 * 1024 * 1024 * 1024),
                'used': float(50 * 1024 * 1024 * 1024),
                'free': float(50 * 1024 * 1024 * 1024),
                'percent': 50.0,
                'framework_usage': float(1 * 1024 * 1024 * 1024)
            },
            'uptime': 3600,
            'system_uptime': 86400,
            'start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'boot_time': (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'),
            'system_version': 'Windows 10 64-bit'
        }
