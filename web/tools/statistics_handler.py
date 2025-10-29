import os, sys, time, uuid, threading
import concurrent.futures
from datetime import datetime, timedelta
from flask import request
historical_data_cache = None
historical_cache_loaded = False
today_data_cache = None
today_cache_time = 0
TODAY_CACHE_DURATION = 600
STATISTICS_CACHE_DURATION = 300

statistics_tasks = {}
task_results = {}

def get_statistics_data(force_refresh=False, add_error_log=None):
    global historical_data_cache, historical_cache_loaded, today_data_cache, today_cache_time
    current_time, current_date = time.time(), datetime.now().date()
    today_cache_valid = not force_refresh and today_data_cache is not None and current_time - today_cache_time < TODAY_CACHE_DURATION
    try:
        if not historical_cache_loaded or historical_data_cache is None:
            historical_data_cache = load_historical_dau_data_optimized(add_error_log)
            historical_cache_loaded = True
        if not today_cache_valid:
            today_data_cache = get_today_dau_data(force_refresh, add_error_log)
            today_cache_time = current_time
        return {'historical': historical_data_cache, 'today': today_data_cache, 'cache_time': current_time,
            'cache_date': current_date.strftime('%Y-%m-%d'), 'cache_info': {'historical_permanently_cached': historical_cache_loaded,
            'today_cached': today_cache_valid, 'today_cache_age': current_time - today_cache_time if today_data_cache else 0,
            'historical_count': len(historical_data_cache) if historical_data_cache else 0}}
    except Exception as e:
        if add_error_log:
            add_error_log(f"获取统计数据失败: {str(e)}")
        return {'historical': [], 'today': {}, 'cache_time': current_time, 'error': str(e)}

def load_historical_dau_data():
    return load_historical_dau_data_from_database()

def load_historical_dau_data_from_database(add_error_log=None):
    try:
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        
        dau_analytics = get_dau_analytics()
        historical_data = []
        
        today = datetime.now()
        
        for i in range(1, 31):
            target_date = today - timedelta(days=i)
            
            try:
                dau_data = dau_analytics.load_dau_data(target_date)
                
                if dau_data:
                    display_date = target_date.strftime('%m-%d')
                    dau_data['display_date'] = display_date
                    
                    if 'event_stats' not in dau_data:
                        dau_data['event_stats'] = {
                            'group_join_count': 0,
                            'group_leave_count': 0,
                            'friend_add_count': 0,
                            'friend_remove_count': 0
                        }
                    
                    historical_data.append(dau_data)
            except Exception as e:
                if add_error_log:
                    add_error_log(f"加载历史DAU数据失败 {target_date.strftime('%Y-%m-%d')}: {str(e)}")
                continue
        
        historical_data.sort(key=lambda x: x.get('date', ''))
        
        return historical_data
        
    except Exception as e:
        if add_error_log:
            add_error_log(f"从数据库加载历史DAU数据失败: {str(e)}")
        return load_historical_dau_data_fallback(add_error_log)

def load_historical_dau_data_optimized(add_error_log=None):
    try:
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            if (project_root := os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))) not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        
        dau_analytics, today = get_dau_analytics(), datetime.now()
        
        def load_single_day_data(days_ago):
            try:
                if dau_data := dau_analytics.load_dau_data(target_date := today - timedelta(days=days_ago)):
                    dau_data['display_date'] = target_date.strftime('%m-%d')
                    return dau_data
            except:
                pass
            return None
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            historical_data = [r for f in concurrent.futures.as_completed({executor.submit(load_single_day_data, i): i for i in range(1, 31)}) if (r := f.result(timeout=5))]
        historical_data.sort(key=lambda x: x.get('date', ''))
        return historical_data
    except Exception as e:
        if add_error_log:
            add_error_log(f"加载优化历史DAU数据失败: {str(e)}")
        return load_historical_dau_data_fallback(add_error_log)

def load_historical_dau_data_fallback(add_error_log=None):
    try:
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            if (project_root := os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))) not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        dau_analytics, today, historical_data = get_dau_analytics(), datetime.now(), []
        for i in range(1, 31):
            try:
                if dau_data := dau_analytics.load_dau_data(target_date := today - timedelta(days=i)):
                    dau_data['display_date'] = target_date.strftime('%m-%d')
                    historical_data.append(dau_data)
            except:
                continue
        historical_data.sort(key=lambda x: x.get('date', ''))
        return historical_data
    except Exception as e:
        if add_error_log:
            add_error_log(f"加载历史DAU数据失败: {str(e)}")
        return []

def get_today_dau_data(force_refresh=False, add_error_log=None):
    try:
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        
        dau_analytics = get_dau_analytics()
        today = datetime.now()
        
        today_data = None
        
        if not force_refresh:
            try:
                today_data = dau_analytics.collect_dau_data(today)
                if today_data:
                    today_data['is_realtime'] = True
                    today_data['cache_time'] = time.time()
                    
                    try:
                        dau_data = dau_analytics.load_dau_data(today)
                        if dau_data and 'event_stats' in dau_data:
                            today_data['event_stats'] = dau_data['event_stats']
                        elif 'event_stats' not in today_data:
                            today_data['event_stats'] = {
                                'group_join_count': 0,
                                'group_leave_count': 0,
                                'friend_add_count': 0,
                                'friend_remove_count': 0
                            }
                    except Exception as e:
                        if 'event_stats' not in today_data:
                            today_data['event_stats'] = {
                                'group_join_count': 0,
                                'group_leave_count': 0,
                                'friend_add_count': 0,
                                'friend_remove_count': 0
                            }
                        
            except Exception as e:
                try:
                    today_data = dau_analytics.load_dau_data(today)
                    if today_data:
                        today_data['from_database'] = True
                except Exception:
                    pass
        
        if not today_data:
            today_data = {
                'message_stats': {
                    'total_messages': 0,
                    'active_users': 0,
                    'active_groups': 0,
                    'private_messages': 0,
                    'peak_hour': 0,
                    'peak_hour_count': 0,
                    'top_groups': [],
                    'top_users': []
                },
                'user_stats': {
                    'total_users': 0,
                    'total_groups': 0,
                    'total_friends': 0
                },
                'command_stats': [],
                'event_stats': {
                    'group_join_count': 0,
                    'group_leave_count': 0,
                    'friend_add_count': 0,
                    'friend_remove_count': 0
                },
                'error': 'No data available'
            }
        
        if today_data and 'event_stats' not in today_data:
            today_data['event_stats'] = {
                'group_join_count': 0,
                'group_leave_count': 0,
                'friend_add_count': 0,
                'friend_remove_count': 0
            }
        
        return today_data or {}
        
    except Exception as e:
        if add_error_log:
            add_error_log(f"获取今日DAU数据失败: {str(e)}")
        return {
            'message_stats': {},
            'user_stats': {},
            'command_stats': [],
            'event_stats': {
                'group_join_count': 0,
                'group_leave_count': 0,
                'friend_add_count': 0,
                'friend_remove_count': 0
            },
            'error': str(e)
        }

def complete_dau_data():
    try:
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            if (project_root := os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))) not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        dau_analytics, today = get_dau_analytics(), datetime.now()
        missing_dates = [target_date for i in range(1, 31) if not dau_analytics.load_dau_data(target_date := today - timedelta(days=i))]
        if not missing_dates:
            return {'generated_count': 0, 'failed_count': 0, 'total_missing': 0, 'generated_dates': [], 
                'failed_dates': [], 'message': '近30天DAU数据完整，无需补全'}
        generated_dates, failed_dates = [], []
        for target_date in missing_dates:
            try:
                (generated_dates if dau_analytics.manual_generate_dau(target_date) else failed_dates).append(target_date.strftime('%Y-%m-%d'))
            except:
                failed_dates.append(target_date.strftime('%Y-%m-%d'))
        return {'generated_count': len(generated_dates), 'failed_count': len(failed_dates), 'total_missing': len(missing_dates),
            'generated_dates': generated_dates, 'failed_dates': failed_dates,
            'message': f'检测到{len(missing_dates)}天的DAU数据缺失，成功生成{len(generated_dates)}天，失败{len(failed_dates)}天'}
    except Exception as e:
        raise Exception(f"补全DAU数据失败: {str(e)}")

def get_available_dau_dates(add_error_log=None):
    try:
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            if (project_root := os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))) not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        dau_analytics, today = get_dau_analytics(), datetime.now().date()
        available_dates = []
        for i in range(31):
            if (check_date := today - timedelta(days=i)) >= today - timedelta(days=30):
                try:
                    if dau_analytics.load_dau_data(datetime.combine(check_date, datetime.min.time())):
                        is_today, date_str = check_date == today, check_date.strftime('%Y-%m-%d')
                        available_dates.append({'value': 'today' if is_today else date_str, 'date': date_str,
                            'display': "今日数据" if is_today else f"{check_date.strftime('%m-%d')} ({date_str})", 'is_today': is_today})
                except:
                    continue
        if not any(item['is_today'] for item in available_dates):
            available_dates.append({'value': 'today', 'date': today.strftime('%Y-%m-%d'), 'display': '今日数据', 'is_today': True})
        available_dates.sort(key=lambda x: (not x['is_today'], -int(x['date'].replace('-', ''))))
        return available_dates
    except Exception as e:
        if add_error_log:
            add_error_log(f"获取可用DAU日期失败: {str(e)}")
        return [{'value': 'today', 'date': datetime.now().strftime('%Y-%m-%d'), 'display': '今日数据', 'is_today': True}]

def get_specific_date_data(date_str, add_error_log=None):
    try:
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            if (project_root := os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))) not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        if not (dau_data := get_dau_analytics().load_dau_data(datetime.strptime(date_str, '%Y-%m-%d'))):
            return None
        return {'message_stats': dau_data.get('message_stats', {}), 'user_stats': dau_data.get('user_stats', {}),
            'command_stats': dau_data.get('command_stats', []), 'event_stats': dau_data.get('event_stats', {}),
            'date': date_str, 'generated_at': dau_data.get('generated_at', ''), 'is_historical': True}
    except Exception as e:
        if add_error_log:
            add_error_log(f"获取特定日期DAU数据失败 {date_str}: {str(e)}")
        return None

def run_statistics_task(task_id, force_refresh=False, selected_date=None, add_error_log=None):
    try:
        statistics_tasks[task_id] = {'status': 'running', 'progress': 0, 'start_time': time.time(), 'message': '开始统计计算...'}
        if selected_date and selected_date != 'today':
            statistics_tasks[task_id].update({'message': f'查询日期 {selected_date} 的数据...', 'progress': 50})
            if not (date_data := get_specific_date_data(selected_date, add_error_log)):
                statistics_tasks[task_id] = {'status': 'failed', 'error': f'未找到日期 {selected_date} 的数据', 'end_time': time.time()}
                return
            result = {'selected_date_data': date_data, 'date': selected_date}
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
        statistics_tasks[task_id] = {'status': 'failed', 'error': str(e), 'end_time': time.time(), 'message': f'统计计算失败: {str(e)}'}

def get_statistics_task_info(task_id):
    if task_id not in statistics_tasks:
        return None
    task_info = statistics_tasks[task_id].copy()
    if task_info['status'] == 'completed' and task_id in task_results:
        return {'status': 'completed', 'progress': 100, 'data': task_results[task_id], 'task_info': task_info}
    if task_info['status'] == 'failed':
        return {'status': 'failed', 'error': task_info.get('error', '未知错误'), 'task_info': task_info}
    return {'status': task_info['status'], 'progress': task_info.get('progress', 0), 
        'message': task_info.get('message', ''), 'elapsed_time': time.time() - task_info.get('start_time', 0)}

def get_all_statistics_tasks_info():
    current_time = time.time()
    tasks_info = {task_id: {**task.copy(), 'elapsed_time': current_time - task['start_time']} if 'start_time' in task else task.copy() 
        for task_id, task in statistics_tasks.items()}
    return {'tasks': tasks_info, 'total_tasks': len(tasks_info), 
        'active_tasks': len([t for t in tasks_info.values() if t['status'] == 'running'])}

def fetch_user_nickname(user_id):
    try:
        if not user_id or len(user_id) < 3:
            return None
        
        try:
            from function.database import Database
        except ImportError:
            if (project_root := os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))) not in sys.path:
                sys.path.insert(0, project_root)
            from function.database import Database
        
        db = Database()
        nickname = db.get_user_name(user_id)
        
        # 只返回有效的昵称（非空且长度合理）
        if nickname and nickname != user_id and 0 < len(nickname) <= 20:
            return nickname
        return None
    except:
        return None

def handle_get_statistics(api_success_response, api_error_response, add_error_log):
    force_refresh, selected_date = request.args.get('force_refresh', 'false').lower() == 'true', request.args.get('date')
    async_mode = request.args.get('async', 'true').lower() == 'true'
    if selected_date and selected_date != 'today':
        return api_success_response({'selected_date_data': date_data, 'date': selected_date}) if (date_data := get_specific_date_data(selected_date, add_error_log)) else api_error_response(f'未找到日期 {selected_date} 的数据', 404)
    if not async_mode:
        start_time = time.time()
        data = get_statistics_data(force_refresh=force_refresh, add_error_log=add_error_log)
        data['performance'] = {'response_time_ms': round((time.time() - start_time) * 1000, 2), 'timestamp': datetime.now().isoformat(), 'optimized': True}
        return api_success_response(data)
    task_id = str(uuid.uuid4())
    threading.Thread(target=lambda: run_statistics_task(task_id, force_refresh, selected_date, add_error_log), daemon=True).start()
    return api_success_response({'task_id': task_id, 'status': 'started', 'message': '统计任务已启动，请使用task_id查询进度'})

def handle_get_statistics_task_status(task_id, api_success_response, api_error_response):
    if task_id not in statistics_tasks:
        return api_error_response('任务不存在或已过期', 404)
    task_info = statistics_tasks[task_id].copy()
    if task_info['status'] == 'completed' and task_id in task_results:
        return api_success_response({'status': 'completed', 'progress': 100, 'data': task_results[task_id], 'task_info': task_info})
    if task_info['status'] == 'failed':
        return api_error_response(task_info.get('error', '未知错误'), 500, task_info=task_info)
    return api_success_response({'status': task_info['status'], 'progress': task_info.get('progress', 0), 
        'message': task_info.get('message', ''), 'elapsed_time': time.time() - task_info.get('start_time', 0)})

def handle_get_all_statistics_tasks(api_success_response):
    return api_success_response(get_all_statistics_tasks_info())

def handle_complete_dau(api_success_response):
    return api_success_response(result=complete_dau_data())

def handle_get_user_nickname(user_id, api_success_response):
    return api_success_response(nickname=fetch_user_nickname(user_id), user_id=user_id)

def handle_get_available_dates(api_success_response, add_error_log):
    return api_success_response(dates=get_available_dau_dates(add_error_log))

