#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SSL证书自动管理 - Let's Encrypt"""

import os, sys, json, time, socket, base64, hashlib, logging, threading, http.client
from datetime import datetime, timedelta
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

logger = logging.getLogger('ElainaBot.ssl')

def _b64encode(data):
    if isinstance(data, str): data = data.encode('utf-8')
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')

class ACMEClient:
    DIRECTORY_URL = 'https://acme-v02.api.letsencrypt.org/directory'
    
    def __init__(self):
        self.directory = self.account_key = self.account_url = self.nonce = None
    
    def _http_request(self, url, method='GET', data=None, headers=None):
        parsed = urlparse(url)
        import ssl
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
        except: ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        
        conn = http.client.HTTPSConnection(parsed.netloc, timeout=60, context=ctx)
        path = parsed.path + ('?' + parsed.query if parsed.query else '')
        req_headers = {'User-Agent': 'ElainaBot-ACME/1.0'}
        if headers: req_headers.update(headers)
        
        try:
            if data:
                if isinstance(data, dict): data = json.dumps(data)
                if isinstance(data, str): data = data.encode('utf-8')
                req_headers['Content-Type'] = 'application/jose+json'
            conn.request(method, path, body=data, headers=req_headers)
            resp = conn.getresponse()
            if resp.getheader('Replay-Nonce'): self.nonce = resp.getheader('Replay-Nonce')
            return {'status': resp.status, 'headers': dict(resp.getheaders()), 'body': resp.read(), 'location': resp.getheader('Location')}
        finally: conn.close()
    
    def _get_directory(self):
        if not self.directory:
            resp = self._http_request(self.DIRECTORY_URL)
            if resp['status'] != 200: raise Exception(f"获取ACME目录失败: {resp['status']}")
            self.directory = json.loads(resp['body'])
        return self.directory
    
    def _get_nonce(self):
        if not self.nonce: self._http_request(self._get_directory()['newNonce'], method='HEAD')
        return self.nonce
    
    def _generate_account_key(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.backends import default_backend
        self.account_key = rsa.generate_private_key(65537, 2048, default_backend())
    
    def _get_jwk(self):
        pub = self.account_key.public_key().public_numbers()
        return {'kty': 'RSA', 'e': _b64encode(pub.e.to_bytes((pub.e.bit_length()+7)//8, 'big')), 'n': _b64encode(pub.n.to_bytes((pub.n.bit_length()+7)//8, 'big'))}
    
    def _get_thumbprint(self):
        return _b64encode(hashlib.sha256(json.dumps(self._get_jwk(), sort_keys=True, separators=(',',':')).encode()).digest())
    
    def _sign_request(self, url, payload):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        protected = {'alg': 'RS256', 'nonce': self._get_nonce(), 'url': url}
        protected['kid' if self.account_url else 'jwk'] = self.account_url or self._get_jwk()
        protected_b64 = _b64encode(json.dumps(protected))
        payload_b64 = '' if payload in [None, ''] else _b64encode(json.dumps(payload))
        sig = self.account_key.sign(f"{protected_b64}.{payload_b64}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return {'protected': protected_b64, 'payload': payload_b64, 'signature': _b64encode(sig)}
    
    def _acme_request(self, url, payload):
        resp = self._http_request(url, method='POST', data=self._sign_request(url, payload))
        self.nonce = None
        return resp
    
    def register_account(self, email):
        if not self.account_key: self._generate_account_key()
        resp = self._acme_request(self._get_directory()['newAccount'], {'termsOfServiceAgreed': True, 'contact': [f'mailto:{email}']})
        if resp['status'] not in [200, 201]:
            body = json.loads(resp['body']) if resp['body'] else {}
            raise Exception(f"注册账户失败: {body.get('detail', resp['status'])}")
        self.account_url = resp['location']
    
    def create_order(self, domain):
        is_ip = False
        try: socket.inet_aton(domain); is_ip = True
        except:
            try: socket.inet_pton(socket.AF_INET6, domain); is_ip = True
            except: pass
        payload = {'identifiers': [{'type': 'ip' if is_ip else 'dns', 'value': domain}]}
        if is_ip: payload['profile'] = 'shortlived'
        resp = self._acme_request(self._get_directory()['newOrder'], payload)
        if resp['status'] not in [200, 201]:
            body = json.loads(resp['body']) if resp['body'] else {}
            raise Exception(f"创建订单失败: {body.get('detail', resp['status'])}")
        order = json.loads(resp['body']); order['url'] = resp['location']
        return order
    
    def get_authorization(self, auth_url):
        resp = self._acme_request(auth_url, '')
        if resp['status'] != 200: raise Exception(f"获取授权失败: {resp['status']}")
        return json.loads(resp['body'])
    
    def get_http_challenge(self, auth):
        for c in auth.get('challenges', []):
            if c['type'] == 'http-01': return {'token': c['token'], 'key_authorization': f"{c['token']}.{self._get_thumbprint()}", 'url': c['url']}
        return None
    
    def respond_challenge(self, url):
        resp = self._acme_request(url, {})
        if resp['status'] not in [200, 202]:
            body = json.loads(resp['body']) if resp['body'] else {}
            raise Exception(f"响应验证失败: {body.get('detail', resp['status'])}")
    
    def poll_authorization(self, auth_url, timeout=120):
        start = time.time()
        while time.time() - start < timeout:
            auth = self.get_authorization(auth_url)
            if auth.get('status') == 'valid': return True
            if auth.get('status') == 'invalid': raise Exception("授权验证失败")
            time.sleep(2)
        raise Exception("授权验证超时")
    
    def poll_order(self, order_url, timeout=120):
        start = time.time()
        while time.time() - start < timeout:
            resp = self._acme_request(order_url, '')
            order = json.loads(resp['body'])
            if order.get('status') in ['ready', 'valid']: return order
            if order.get('status') == 'invalid': raise Exception("订单失败")
            time.sleep(2)
        raise Exception("订单处理超时")
    
    def finalize_order(self, url, csr_der):
        resp = self._acme_request(url, {'csr': _b64encode(csr_der)})
        if resp['status'] not in [200, 201]:
            body = json.loads(resp['body']) if resp['body'] else {}
            raise Exception(f"完成订单失败: {body.get('detail', resp['status'])}")
        return json.loads(resp['body'])
    
    def download_certificate(self, url):
        resp = self._acme_request(url, '')
        if resp['status'] != 200: raise Exception(f"下载证书失败: {resp['status']}")
        return resp['body'].decode('utf-8')
    
    def generate_csr(self, domain, key):
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.backends import default_backend
        import ipaddress
        try: ip = ipaddress.ip_address(domain); is_ip = True
        except: is_ip = False
        if is_ip:
            subject = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'ElainaBot')])
            san = x509.SubjectAlternativeName([x509.IPAddress(ip)])
        else:
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
            san = x509.SubjectAlternativeName([x509.DNSName(domain)])
        builder = x509.CertificateSigningRequestBuilder().subject_name(subject).add_extension(san, critical=False)
        return builder.sign(key, hashes.SHA256(), default_backend()).public_bytes(serialization.Encoding.DER)
    
    def generate_private_key(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.backends import default_backend
        return rsa.generate_private_key(65537, 2048, default_backend())


class SSLManager:
    def __init__(self, config):
        self.config = config
        self.domain = config.get('ssl_domain', '')
        self.email = config.get('ssl_email', '')
        self.cert_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'ssl')
        self.ssl_port = config.get('ssl_port', 8443)
        self.renew_days = config.get('ssl_renew_days', 2)
        self.check_hour = config.get('ssl_check_hour', 12)
        
        self.cert_file = os.path.join(self.cert_dir, 'cert.pem')
        self.key_file = os.path.join(self.cert_dir, 'key.pem')
        self.account_key_file = os.path.join(self.cert_dir, 'account.key')
        self.challenge_dir = os.path.join(self.cert_dir, '.well-known', 'acme-challenge')
        
        self._challenge_server = None
        self._check_thread = None
        self._running = False
        self._cert_updated = False
        self._last_renew_time = None
        self._precheck_state = None
        
        os.makedirs(self.cert_dir, exist_ok=True)
        os.makedirs(self.challenge_dir, exist_ok=True)
    
    def get_cert_expiry(self):
        if not os.path.exists(self.cert_file): return None
        try:
            from cryptography import x509
            from cryptography.hazmat.backends import default_backend
            with open(self.cert_file, 'rb') as f:
                return x509.load_pem_x509_certificate(f.read(), default_backend()).not_valid_after
        except: return None
    
    def get_cert_remaining_days(self):
        expiry = self.get_cert_expiry()
        return (expiry - datetime.utcnow()).days if expiry else -1
    
    def _start_challenge_server(self):
        if self._challenge_server: return True
        
        # 检查80端口
        port_occupied = False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            port_occupied = sock.connect_ex(('127.0.0.1', 80)) == 0
            sock.close()
        except: pass
        
        if port_occupied:
            # 使用nginx webroot
            for path in ['/www/server/nginx/html', '/usr/share/nginx/html', '/var/www/html']:
                if os.path.isdir(path):
                    self.challenge_dir = os.path.join(path, '.well-known', 'acme-challenge')
                    try:
                        os.makedirs(self.challenge_dir, exist_ok=True)
                        logger.info(f"使用nginx webroot: {self.challenge_dir}")
                        return True
                    except PermissionError:
                        import subprocess
                        try:
                            subprocess.run(['sudo', 'mkdir', '-p', self.challenge_dir], check=True, timeout=5)
                            subprocess.run(['sudo', 'chmod', '777', self.challenge_dir], check=True, timeout=5)
                            logger.info(f"使用nginx webroot(sudo): {self.challenge_dir}")
                            return True
                        except: pass
            logger.error("80端口被占用且无法使用nginx webroot")
            return False
        
        # 直接监听80端口
        return self._start_http_server(80)
    
    def _start_http_server(self, port):
        challenge_dir = self.challenge_dir
        cert_dir = self.cert_dir
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs): super().__init__(*args, directory=cert_dir, **kwargs)
            def log_message(self, *args): pass
            def do_GET(self):
                if self.path.startswith('/.well-known/acme-challenge/'):
                    token_file = os.path.join(challenge_dir, self.path.split('/')[-1])
                    if os.path.exists(token_file):
                        with open(token_file, 'r') as f:
                            self.send_response(200); self.send_header('Content-Type', 'text/plain'); self.end_headers()
                            self.wfile.write(f.read().encode()); return
                self.send_response(404); self.end_headers()
        try:
            self._challenge_server = HTTPServer(('0.0.0.0', port), Handler)
            threading.Thread(target=self._challenge_server.serve_forever, daemon=True).start()
            time.sleep(0.5)
            logger.info(f"ACME验证服务器启动: 端口 {port}")
            return True
        except Exception as e:
            logger.error(f"启动验证服务器失败: {e}")
            return False
    
    def _stop_challenge_server(self):
        if self._challenge_server:
            try: self._challenge_server.shutdown()
            except: pass
            self._challenge_server = None
    
    def _write_token(self, path, content):
        try:
            with open(path, 'w') as f: f.write(content)
        except PermissionError:
            import subprocess
            subprocess.run(f"echo -n '{content}' | sudo tee {path} > /dev/null", shell=True, check=True, timeout=5)
    
    def _remove_token(self, path):
        try: os.remove(path)
        except:
            import subprocess
            subprocess.run(['sudo', 'rm', '-f', path], capture_output=True, timeout=5)
    
    def request_certificate(self):
        if not self.domain or not self.email:
            logger.error("未配置域名或邮箱"); return False
        
        logger.info(f"申请证书: {self.domain}")
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.backends import default_backend
            
            acme = ACMEClient()
            
            # 加载或创建账户密钥
            if os.path.exists(self.account_key_file):
                with open(self.account_key_file, 'rb') as f:
                    acme.account_key = serialization.load_pem_private_key(f.read(), None, default_backend())
            else:
                acme._generate_account_key()
                with open(self.account_key_file, 'wb') as f:
                    f.write(acme.account_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
            
            acme.register_account(self.email)
            order = acme.create_order(self.domain)
            
            if not self._start_challenge_server(): return False
            
            try:
                for auth_url in order.get('authorizations', []):
                    auth = acme.get_authorization(auth_url)
                    challenge = acme.get_http_challenge(auth)
                    if not challenge: raise Exception("未找到HTTP-01验证")
                    
                    token_file = os.path.join(self.challenge_dir, challenge['token'])
                    self._write_token(token_file, challenge['key_authorization'])
                    acme.respond_challenge(challenge['url'])
                    acme.poll_authorization(auth_url)
                    self._remove_token(token_file)
                
                order = acme.poll_order(order['url'])
                domain_key = acme.generate_private_key()
                
                with open(self.key_file, 'wb') as f:
                    f.write(domain_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
                
                order = acme.finalize_order(order['finalize'], acme.generate_csr(self.domain, domain_key))
                if order.get('status') != 'valid': order = acme.poll_order(order['url'])
                
                with open(self.cert_file, 'w') as f: f.write(acme.download_certificate(order['certificate']))
                
                logger.info(f"✅ 证书申请成功！有效期至: {self.get_cert_expiry()}")
                self._cert_updated = True
                self._last_renew_time = datetime.now()
                self._auto_restart()
                return True
            finally:
                self._stop_challenge_server()
        except Exception as e:
            logger.error(f"证书申请失败: {e}")
            return False

    
    def prepare_precheck(self):
        """准备预验证"""
        if not self.domain or not self.email:
            return {'success': False, 'error': '未配置域名或邮箱'}
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.backends import default_backend
            
            logger.info("准备预验证...")
            acme = ACMEClient()
            
            if os.path.exists(self.account_key_file):
                with open(self.account_key_file, 'rb') as f:
                    acme.account_key = serialization.load_pem_private_key(f.read(), None, default_backend())
            else:
                acme._generate_account_key()
                with open(self.account_key_file, 'wb') as f:
                    f.write(acme.account_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
            
            acme.register_account(self.email)
            order = acme.create_order(self.domain)
            
            if not self._start_challenge_server():
                return {'success': False, 'error': '无法启动验证服务'}
            
            for auth_url in order.get('authorizations', []):
                auth = acme.get_authorization(auth_url)
                challenge = acme.get_http_challenge(auth)
                if not challenge: return {'success': False, 'error': '未找到HTTP-01验证'}
                
                token_file = os.path.join(self.challenge_dir, challenge['token'])
                self._write_token(token_file, challenge['key_authorization'])
                
                self._precheck_state = {'acme': acme, 'order': order, 'challenge': challenge, 'auth_url': auth_url, 'token_file': token_file}
                verify_url = f"http://{self.domain}/.well-known/acme-challenge/{challenge['token']}"
                logger.info(f"预验证文件已准备: {verify_url}")
                return {'success': True, 'verify_url': verify_url, 'challenge_token': challenge['token']}
            
            return {'success': False, 'error': '无法获取验证信息'}
        except Exception as e:
            logger.error(f"准备预验证失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def cancel_precheck(self):
        if self._precheck_state:
            self._remove_token(self._precheck_state.get('token_file', ''))
            self._precheck_state = None
        self._stop_challenge_server()
    
    def confirm_renew(self):
        """确认续签"""
        if not self._precheck_state:
            return {'success': False, 'error': '没有待确认的预验证'}
        try:
            from cryptography.hazmat.primitives import serialization
            
            acme = self._precheck_state['acme']
            order = self._precheck_state['order']
            challenge = self._precheck_state['challenge']
            auth_url = self._precheck_state['auth_url']
            token_file = self._precheck_state['token_file']
            
            logger.info("确认续签...")
            acme.respond_challenge(challenge['url'])
            acme.poll_authorization(auth_url)
            self._remove_token(token_file)
            
            order = acme.poll_order(order['url'])
            domain_key = acme.generate_private_key()
            
            with open(self.key_file, 'wb') as f:
                f.write(domain_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
            
            order = acme.finalize_order(order['finalize'], acme.generate_csr(self.domain, domain_key))
            if order.get('status') != 'valid': order = acme.poll_order(order['url'])
            
            with open(self.cert_file, 'w') as f: f.write(acme.download_certificate(order['certificate']))
            
            logger.info(f"✅ 证书申请成功！有效期至: {self.get_cert_expiry()}")
            self._precheck_state = None
            self._stop_challenge_server()
            self._cert_updated = True
            self._last_renew_time = datetime.now()
            self._auto_restart()
            return {'success': True}
        except Exception as e:
            logger.error(f"确认续签失败: {e}")
            self._precheck_state = None
            return {'success': False, 'error': str(e)}
    
    def _check_and_renew(self):
        remaining = self.get_cert_remaining_days()
        if remaining < 0:
            logger.info("证书不存在，开始申请...")
            self.request_certificate()
        elif remaining <= self.renew_days:
            logger.info(f"证书剩余 {remaining} 天，开始续签...")
            self.request_certificate()
        else:
            logger.info(f"证书剩余 {remaining} 天")
    
    def _schedule_check(self):
        while self._running:
            now = datetime.now()
            next_check = now.replace(hour=self.check_hour, minute=0, second=0, microsecond=0)
            if now >= next_check: next_check += timedelta(days=1)
            wait = (next_check - now).total_seconds()
            logger.info(f"下次检查: {next_check.strftime('%Y-%m-%d %H:%M:%S')}")
            waited = 0
            while waited < wait and self._running:
                time.sleep(min(60, wait - waited)); waited += 60
            if self._running: self._check_and_renew()
    
    def start(self):
        if not self.config.get('ssl_auto_cert') or not self.domain: return
        self._running = True
        logger.info("SSL证书管理器启动...")
        self._check_and_renew()
        self._check_thread = threading.Thread(target=self._schedule_check, daemon=True)
        self._check_thread.start()
    
    def stop(self):
        self._running = False
        self._stop_challenge_server()
    
    def get_status(self):
        expiry = self.get_cert_expiry()
        return {
            'auto_cert': self.config.get('ssl_auto_cert', False),
            'domain': self.domain, 'email': self.email,
            'cert_exists': os.path.exists(self.cert_file),
            'expiry': expiry.strftime('%Y-%m-%d %H:%M:%S') if expiry else None,
            'remaining_days': self.get_cert_remaining_days(),
            'ssl_port': self.ssl_port, 'check_hour': self.check_hour, 'renew_days': self.renew_days,
            'cert_updated': self._cert_updated,
            'last_renew_time': self._last_renew_time.strftime('%Y-%m-%d %H:%M:%S') if self._last_renew_time else None
        }
    
    def force_renew(self):
        logger.info("手动触发证书续签...")
        return self.request_certificate()
    
    def _auto_restart(self):
        try:
            from web.tools.bot_restart import execute_bot_restart
            logger.info("🔄 证书已更新，正在自动重启...")
            execute_bot_restart({'restart_time': datetime.now().isoformat(), 'completed': False, 'message_id': None, 'user_id': 'ssl_manager', 'group_id': 'system', 'reason': 'SSL证书自动续签'})
        except Exception as e:
            logger.error(f"自动重启失败: {e}")

_ssl_manager = None

def init_ssl_manager(config):
    global _ssl_manager
    _ssl_manager = SSLManager(config)
    return _ssl_manager

def get_ssl_manager(): return _ssl_manager
def start_ssl_manager():
    if _ssl_manager: _ssl_manager.start()
def stop_ssl_manager():
    if _ssl_manager: _ssl_manager.stop()
