import os, sys, time, uuid, threading, concurrent.futures
from datetime import datetime, timedelta
from flask import request

historical_data_cache = None
historical_cache_loaded = False
today_data_cache = None
today_cache_time = 0
statistics_tasks = {}
task_results = {}

_TODAY_CACHE_DURATION = 600
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_EVENT_STATS = {'group_join_count': 0, 'group_leave_count': 0, 'friend_add_count': 0, 'friend_remove_count': 0}

def _ensure_path():
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)

def _get_dau_analytics():
    _ensure_path()
    from function.dau_analytics import get_dau_analytics
    return get_dau_analytics()

def get_statistics_data(force_refresh=False, add_error_log=None):
    global historical_data_cache, historical_cache_loaded, today_data_cache, today_cache_time
    now = time.time()
    today_valid = not force_refresh and today_data_cache and now - today_cache_time < _TODAY_CACHE_DURATION
    
    try:
        if not historical_cache_loaded or not historical_data_cache:
            historical_data_cache = load_historical_dau_data_optimized(add_error_log)
            historical_cache_loaded = True
        if not today_valid:
            today_data_cache = get_today_dau_data(force_refresh, add_error_log)
            today_cache_time = now
        
        return {'historical': historical_data_cache, 'today': today_data_cache, 'cache_time': now,
                'cache_date': datetime.now().strftime('%Y-%m-%d'),
                'cache_info': {'historical_permanently_cached': historical_cache_loaded, 'today_cached': today_valid,
                               'today_cache_age': now - today_cache_time if today_data_cache else 0,
                               'historical_count': len(historical_data_cache) if historical_data_cache else 0}}
    except Exception as e:
        if add_error_log:
            add_error_log(f"获取统计数据失败: {e}")
        return {'historical': [], 'today': {}, 'cache_time': now, 'error': str(e)}

def load_historical_dau_data_optimized(add_error_log=None):
    try:
        dau, today = _get_dau_analytics(), datetime.now()
        
        def load_day(i):
            try:
                if data := dau.load_dau_data(d := today - timedelta(days=i)):
                    data['display_date'] = d.strftime('%m-%d')
                    return data
            except:
                pass
            return None
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            results = [r for f in concurrent.futures.as_completed({ex.submit(load_day, i): i for i in range(1, 31)}) if (r := f.result(timeout=5))]
        results.sort(key=lambda x: x.get('date', ''))
        return results
    except Exception as e:
        if add_error_log:
            add_error_log(f"加载历史DAU数据失败: {e}")
        return []

def get_today_dau_data(force_refresh=False, add_error_log=None):
    try:
        dau, today = _get_dau_analytics(), datetime.now()
        data = None
        
        if not force_refresh:
            try:
                if data := dau.collect_dau_data(today):
                    data['is_realtime'] = True
                    data['cache_time'] = time.time()
                    try:
                        if saved := dau.load_dau_data(today):
                            data['event_stats'] = saved.get('event_stats', _DEFAULT_EVENT_STATS.copy())
                    except:
                        pass
            except:
                try:
                    data = dau.load_dau_data(today)
                    if data:
                        data['from_database'] = True
                except:
                    pass
        
        if not data:
            data = {'message_stats': {'total_messages': 0, 'active_users': 0, 'active_groups': 0, 'private_messages': 0, 'peak_hour': 0, 'peak_hour_count': 0, 'top_groups': [], 'top_users': []},
                    'user_stats': {'total_users': 0, 'total_groups': 0, 'total_friends': 0}, 'command_stats': [], 'event_stats': _DEFAULT_EVENT_STATS.copy(), 'error': 'No data available'}
        
        if 'event_stats' not in data:
            data['event_stats'] = _DEFAULT_EVENT_STATS.copy()
        return data
    except Exception as e:
        if add_error_log:
            add_error_log(f"获取今日DAU数据失败: {e}")
        return {'message_stats': {}, 'user_stats': {}, 'command_stats': [], 'event_stats': _DEFAULT_EVENT_STATS.copy(), 'error': str(e)}

def complete_dau_data():
    try:
        dau, today = _get_dau_analytics(), datetime.now()
        missing = []
        for i in range(1, 31):
            d = today - timedelta(days=i)
            data = dau.load_dau_data(d)
            # 如果没有数据，或者数据无效（total_messages 为 0 但 message_stats_detail 为空）
            if not data:
                missing.append(d)
            elif data.get('message_stats', {}).get('total_messages', 0) == 0:
                # 检查是否是空记录（只有事件统计，没有消息统计）
                ms = data.get('message_stats', {})
                if not ms.get('top_groups') and not ms.get('top_users'):
                    missing.append(d)
        
        if not missing:
            return {'generated_count': 0, 'failed_count': 0, 'total_missing': 0, 'generated_dates': [], 'failed_dates': [], 'message': '近30天DAU数据完整，无需补全'}
        
        gen, fail = [], []
        for d in missing:
            try:
                (gen if dau.manual_generate_dau(d) else fail).append(d.strftime('%Y-%m-%d'))
            except:
                fail.append(d.strftime('%Y-%m-%d'))
        
        return {'generated_count': len(gen), 'failed_count': len(fail), 'total_missing': len(missing),
                'generated_dates': gen, 'failed_dates': fail,
                'message': f'检测到{len(missing)}天的DAU数据缺失或无效，成功生成{len(gen)}天，失败{len(fail)}天'}
    except Exception as e:
        raise Exception(f"补全DAU数据失败: {e}")

def get_available_dau_dates(add_error_log=None):
    try:
        dau, today = _get_dau_analytics(), datetime.now().date()
        dates = []
        for i in range(31):
            d = today - timedelta(days=i)
            if d >= today - timedelta(days=30):
                try:
                    if dau.load_dau_data(datetime.combine(d, datetime.min.time())):
                        is_today = d == today
                        dates.append({'value': 'today' if is_today else d.strftime('%Y-%m-%d'), 'date': d.strftime('%Y-%m-%d'),
                                      'display': "今日数据" if is_today else f"{d.strftime('%m-%d')} ({d.strftime('%Y-%m-%d')})", 'is_today': is_today})
                except:
                    continue
        
        if not any(x['is_today'] for x in dates):
            dates.append({'value': 'today', 'date': today.strftime('%Y-%m-%d'), 'display': '今日数据', 'is_today': True})
        dates.sort(key=lambda x: (not x['is_today'], -int(x['date'].replace('-', ''))))
        return dates
    except Exception as e:
        if add_error_log:
            add_error_log(f"获取可用DAU日期失败: {e}")
        return [{'value': 'today', 'date': datetime.now().strftime('%Y-%m-%d'), 'display': '今日数据', 'is_today': True}]

def get_specific_date_data(date_str, add_error_log=None):
    try:
        if data := _get_dau_analytics().load_dau_data(datetime.strptime(date_str, '%Y-%m-%d')):
            return {'message_stats': data.get('message_stats', {}), 'user_stats': data.get('user_stats', {}),
                    'command_stats': data.get('command_stats', []), 'event_stats': data.get('event_stats', {}),
                    'date': date_str, 'generated_at': data.get('generated_at', ''), 'is_historical': True}
        return None
    except Exception as e:
        if add_error_log:
            add_error_log(f"获取特定日期DAU数据失败 {date_str}: {e}")
        return None


def run_statistics_task(task_id, force_refresh=False, selected_date=None, add_error_log=None):
    try:
        statistics_tasks[task_id] = {'status': 'running', 'progress': 0, 'start_time': time.time(), 'message': '开始统计计算...'}
        
        if selected_date and selected_date != 'today':
            statistics_tasks[task_id].update({'message': f'查询日期 {selected_date} 的数据...', 'progress': 50})
            if not (data := get_specific_date_data(selected_date, add_error_log)):
                statistics_tasks[task_id] = {'status': 'failed', 'error': f'未找到日期 {selected_date} 的数据', 'end_time': time.time()}
                return
            result = {'selected_date_data': data, 'date': selected_date}
        else:
            statistics_tasks[task_id].update({'message': '获取历史数据...', 'progress': 30})
            data = get_statistics_data(force_refresh=force_refresh, add_error_log=add_error_log)
            statistics_tasks[task_id].update({'message': '计算性能指标...', 'progress': 80})
            data['performance'] = {'response_time_ms': round((time.time() - statistics_tasks[task_id]['start_time']) * 1000, 2),
                                   'timestamp': datetime.now().isoformat(), 'optimized': True, 'async': True}
            result = data
        
        statistics_tasks[task_id] = {'status': 'completed', 'progress': 100, 'end_time': time.time(), 'message': '统计计算完成'}
        task_results[task_id] = result
        
        for tid in [t for t, task in statistics_tasks.items() if task.get('end_time', 0) < time.time() - 3600]:
            statistics_tasks.pop(tid, None)
            task_results.pop(tid, None)
    except Exception as e:
        statistics_tasks[task_id] = {'status': 'failed', 'error': str(e), 'end_time': time.time(), 'message': f'统计计算失败: {e}'}

def fetch_user_nickname(user_id):
    if not user_id or len(user_id) < 3:
        return None
    try:
        _ensure_path()
        from function.database import Database
        nick = Database().get_user_name(user_id)
        return nick if nick and nick != user_id and 0 < len(nick) <= 20 else None
    except:
        return None

def handle_get_statistics(api_success_response, api_error_response, add_error_log):
    force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'
    selected_date = request.args.get('date')
    async_mode = request.args.get('async', 'true').lower() == 'true'
    
    if selected_date and selected_date != 'today':
        data = get_specific_date_data(selected_date, add_error_log)
        return api_success_response({'selected_date_data': data, 'date': selected_date}) if data else api_error_response(f'未找到日期 {selected_date} 的数据', 404)
    
    if not async_mode:
        start = time.time()
        data = get_statistics_data(force_refresh=force_refresh, add_error_log=add_error_log)
        data['performance'] = {'response_time_ms': round((time.time() - start) * 1000, 2), 'timestamp': datetime.now().isoformat(), 'optimized': True}
        return api_success_response(data)
    
    task_id = str(uuid.uuid4())
    threading.Thread(target=lambda: run_statistics_task(task_id, force_refresh, selected_date, add_error_log), daemon=True).start()
    return api_success_response({'task_id': task_id, 'status': 'started', 'message': '统计任务已启动，请使用task_id查询进度'})

def handle_get_statistics_task_status(task_id, api_success_response, api_error_response):
    if task_id not in statistics_tasks:
        return api_error_response('任务不存在或已过期', 404)
    
    task = statistics_tasks[task_id].copy()
    if task['status'] == 'completed' and task_id in task_results:
        return api_success_response({'status': 'completed', 'progress': 100, 'data': task_results[task_id], 'task_info': task})
    if task['status'] == 'failed':
        return api_error_response(task.get('error', '未知错误'), 500, task_info=task)
    return api_success_response({'status': task['status'], 'progress': task.get('progress', 0),
                                 'message': task.get('message', ''), 'elapsed_time': time.time() - task.get('start_time', 0)})

def handle_get_all_statistics_tasks(api_success_response):
    now = time.time()
    tasks = {tid: {**t.copy(), 'elapsed_time': now - t['start_time']} if 'start_time' in t else t.copy() for tid, t in statistics_tasks.items()}
    return api_success_response({'tasks': tasks, 'total_tasks': len(tasks), 'active_tasks': sum(1 for t in tasks.values() if t['status'] == 'running')})

def handle_complete_dau(api_success_response):
    return api_success_response(result=complete_dau_data())

def handle_get_user_nickname(user_id, api_success_response):
    return api_success_response(nickname=fetch_user_nickname(user_id), user_id=user_id)

def handle_get_available_dates(api_success_response, add_error_log):
    return api_success_response(dates=get_available_dau_dates(add_error_log))
