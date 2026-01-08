import os, gc, time, psutil, platform
from datetime import datetime, timedelta

START_TIME = datetime.now()
add_error_log = None

_last_gc_time = 0
_GC_INTERVAL = 30
_FRAMEWORK_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_IS_WINDOWS = platform.system() == 'Windows'
_IS_LINUX = platform.system() == 'Linux'

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
        return "连接成功" if client and hasattr(client, 'connected') and client.connected else "连接失败"
    except:
        return "连接失败"

def get_cpu_model():
    try:
        if _IS_WINDOWS:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            model = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
            winreg.CloseKey(key)
            return model
        elif _IS_LINUX:
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if 'model name' in line.lower():
                        return line.split(':', 1)[1].strip()
    except:
        pass
    return "未知处理器"

def get_disk_info():
    try:
        disk = psutil.disk_usage(os.path.abspath(os.getcwd()))
        framework_size = sum(os.path.getsize(os.path.join(r, f)) for r, _, files in os.walk(_FRAMEWORK_ROOT) for f in files if os.path.isfile(os.path.join(r, f)))
        return {'total': float(disk.total), 'used': float(disk.used), 'free': float(disk.free), 'percent': float(disk.percent), 'framework_usage': float(framework_size)}
    except:
        return {'total': 100*1024**3, 'used': 50*1024**3, 'free': 50*1024**3, 'percent': 50.0, 'framework_usage': 1024**3}

def get_system_info():
    global _last_gc_time
    
    try:
        process, now = psutil.Process(os.getpid()), time.time()
        
        if now - _last_gc_time >= _GC_INTERVAL:
            gc.collect(0)
            _last_gc_time = now
        
        mem = process.memory_info()
        rss_mb = mem.rss / 1024 / 1024
        sys_mem = psutil.virtual_memory()
        sys_mem_total_mb = sys_mem.total / 1024 / 1024
        
        try:
            cpu_cores = psutil.cpu_count(logical=True)
            cpu_pct = max(process.cpu_percent(interval=0.05), 1.0)
            sys_cpu_pct = max(psutil.cpu_percent(interval=0.05), 5.0)
        except:
            cpu_cores, cpu_pct, sys_cpu_pct = 1, 1.0, 5.0
        
        app_uptime = int((datetime.now() - START_TIME).total_seconds())
        
        try:
            boot = datetime.fromtimestamp(psutil.boot_time())
            sys_uptime, boot_str = int((datetime.now() - boot).total_seconds()), boot.strftime('%Y-%m-%d %H:%M:%S')
        except:
            sys_uptime, boot_str = app_uptime, datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        return {
            'cpu_percent': float(sys_cpu_pct), 'framework_cpu_percent': float(cpu_pct),
            'cpu_cores': cpu_cores, 'cpu_model': get_cpu_model(),
            'memory_percent': float(sys_mem.percent), 'memory_used': float(sys_mem.used / 1024 / 1024),
            'memory_total': float(sys_mem_total_mb), 'total_memory': float(sys_mem_total_mb),
            'system_memory_total_bytes': float(sys_mem.total),
            'framework_memory_percent': float((rss_mb / sys_mem_total_mb) * 100 if sys_mem_total_mb > 0 else 5.0),
            'framework_memory_total': float(rss_mb),
            'gc_counts': list(gc.get_count()), 'objects_count': len(gc.get_objects()),
            'disk_info': get_disk_info(),
            'uptime': app_uptime, 'system_uptime': sys_uptime,
            'start_time': START_TIME.strftime('%Y-%m-%d %H:%M:%S'), 'boot_time': boot_str,
            'system_version': platform.platform()
        }
    except Exception as e:
        if add_error_log:
            add_error_log(f"获取系统信息失败: {e}")
        return {
            'cpu_percent': 5.0, 'framework_cpu_percent': 1.0, 'cpu_cores': 4, 'cpu_model': '未知处理器',
            'memory_percent': 50.0, 'memory_used': 400.0, 'memory_total': 8192.0, 'total_memory': 8192.0,
            'system_memory_total_bytes': 8192.0 * 1024 * 1024, 'framework_memory_percent': 5.0, 'framework_memory_total': 400.0,
            'gc_counts': [0, 0, 0], 'objects_count': 1000,
            'disk_info': {'total': 100*1024**3, 'used': 50*1024**3, 'free': 50*1024**3, 'percent': 50.0, 'framework_usage': 1024**3},
            'uptime': 3600, 'system_uptime': 86400,
            'start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'boot_time': (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'),
            'system_version': 'Windows 10 64-bit'
        }
