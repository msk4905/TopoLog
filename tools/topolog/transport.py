"""GNS3 API and bounded Telnet console access, no shell command execution."""
import base64
import configparser
import json
import os
from pathlib import Path
import re
import socket
import time
import urllib.request
import urllib.parse

PROMPT = re.compile(r'(?m)^([\w.:/-]+[>#])\s*\Z')
CLI_ERROR = re.compile(r'(?im)^\s*(?:%\s*(?:Invalid|Ambiguous|Incomplete|Authorization|Unrecognized|Error)|ERROR:|Unknown command|Invalid command)')
PAGER = re.compile(r'--More--|<---\s*More\s*--->')
# A pager prompt still at the end of the visible screen: the device has not
# consumed the key yet (IOS erases --More-- with backspaces once it reads it).
PAGER_WAITING = re.compile(r'(?:--More--|<---\s*More\s*--->)\s*\Z')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('API redirects are not accepted')


class API:
    def __init__(self, base=None):
        config = configparser.ConfigParser(interpolation=None)
        config.read(Path(os.environ.get('APPDATA', Path.home() / '.config')) / 'GNS3/2.2/gns3_server.ini', encoding='utf-8-sig')
        values = config['Server'] if config.has_section('Server') else {}
        host = values.get('host', '127.0.0.1')
        if host in ('0.0.0.0', '::'):
            host = '127.0.0.1'
        configured = '{}://{}:{}'.format(values.get('protocol', 'http'), host, values.get('port', '3080'))
        self.base = (base or configured).rstrip('/')
        uri = urllib.parse.urlsplit(self.base)
        if uri.scheme not in ('http', 'https') or not uri.hostname or uri.username or uri.password or uri.path or uri.query or uri.fragment:
            raise ValueError('Use an HTTP(S) server origin without credentials or path')
        self.host = uri.hostname
        self.headers = {}
        if self.base == configured and values.get('auth', '').lower() in ('true', 'yes', '1'):
            pair = values.get('user', '') + ':' + values.get('password', '')
            self.headers['Authorization'] = 'Basic ' + base64.b64encode(pair.encode()).decode()
        # GNS3 is a directly addressed controller, not an internet web request.
        # System proxy auto-discovery can send host-only adapter traffic to a proxy.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def raw(self, path, method='GET', body=None):
        headers = dict(self.headers)
        data = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(body).encode()
        elif method == 'POST':
            headers['Content-Type'] = 'application/json'
            data = b'{}'
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        with self.opener.open(request, timeout=20) as response:
            return response.read()

    def call(self, path, method='GET', body=None):
        data = self.raw(path, method, body)
        return json.loads(data) if data else None


class TelnetCodec:
    def __init__(self):
        self.state = 0
        self.verb = 0
        self.remote = set()

    def feed(self, data):
        output, reply = bytearray(), bytearray()
        for byte in data:
            if self.state == 0:
                if byte == 255:
                    self.state = 1
                else:
                    output.append(byte)
            elif self.state == 1:
                if byte == 255:
                    output.append(byte)
                    self.state = 0
                elif byte in (251, 252, 253, 254):
                    self.verb = byte
                    self.state = 2
                elif byte == 250:
                    self.state = 3
                else:
                    self.state = 0
            elif self.state == 2:
                if self.verb == 251:
                    if byte not in self.remote:
                        reply.extend((255, 253 if byte in (1, 3) else 254, byte))
                    if byte in (1, 3):
                        self.remote.add(byte)
                elif self.verb == 253:
                    reply.extend((255, 252, byte))
                elif self.verb == 252:
                    self.remote.discard(byte)
                self.state = 0
            elif self.state == 3:
                if byte == 255:
                    self.state = 4
            elif self.state == 4:
                self.state = 0 if byte == 240 else 3
        return output, reply


class EnableSetupRequired(ValueError):
    """The device asks to create a new enable password (e.g. first ASA enable)."""


def visible(text):
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text).replace('\r', '').replace('\x00', '')
    result = []
    for character in text:
        if character == '\b':
            if result and result[-1] != '\n':
                result.pop()
        else:
            result.append(character)
    return ''.join(result)


class Console:
    def __init__(self, host, port, ready_timeout=120, command_timeout=30, cancel=None, progress=None,
                 pager_retry=1.0, pager_retries=3):
        self.host, self.port = host, int(port)
        self.ready_timeout, self.command_timeout = ready_timeout, command_timeout
        # A key sent the moment --More-- arrives can be dropped by a slow emulated
        # console; it is re-sent only while the prompt is still unanswered.
        self.pager_retry, self.pager_retries = pager_retry, pager_retries
        self.pager_resends = 0
        self.cancel = cancel
        self.progress = progress or (lambda message: None)
        self.wire = bytearray()
        self.sock = None
        self.awaiting_prompt = False

    def _check_cancel(self):
        if self.cancel and self.cancel.is_set():
            raise InterruptedError('Collection cancelled')

    def _read(self, timeout, allow_password=False):
        end = time.monotonic() + timeout
        received = bytearray()
        pages = 0
        page_key_at, page_resends = 0.0, 0
        next_update = time.monotonic() + 5
        while time.monotonic() < end:
            self._check_cancel()
            if time.monotonic() >= next_update:
                self.progress('Waiting for CLI prompt ({}s remaining)'.format(max(0, int(end - time.monotonic()))))
                next_update = time.monotonic() + 5
            try:
                chunk = self.sock.recv(8192)
            except socket.timeout:
                text = visible(received.decode('utf-8', errors='replace'))
                match = PROMPT.search(text)
                if match:
                    return text, match.group(1)
                if (pages and page_resends < self.pager_retries and PAGER_WAITING.search(text)
                        and time.monotonic() - page_key_at >= self.pager_retry):
                    self.sock.sendall(b' ')
                    page_key_at = time.monotonic()
                    page_resends += 1
                    self.pager_resends += 1
                    self.progress('CLI pagination: page {} key re-sent ({})'.format(pages, page_resends))
                continue
            if not chunk:
                raise ConnectionError('Console disconnected')
            self.wire.extend(chunk)
            if len(self.wire) > 8 * 1024 * 1024:
                raise ValueError('Console receive limit exceeded')
            data, reply = self.codec.feed(chunk)
            if reply:
                self.sock.sendall(reply)
            received.extend(data)
            text = received.decode('utf-8', errors='replace')
            if allow_password and re.search(r'(?im)^[ \t]*(?:Enter|Repeat)\s+Password:\s*\Z', visible(text)):
                # Answering would create/change the enable password: a configuration change.
                return visible(text), '<set-password>'
            if re.search(r'(?im)^[ \t]*(?:username|password|login):\s*\Z', text):
                if allow_password and re.search(r'(?im)^[ \t]*Password:\s*\Z', visible(text)):
                    return visible(text), '<password>'
                raise ValueError('Console authentication required')
            if re.search(r'(?im)\(config[^\n]*\)[#>]\s*$', text):
                raise ValueError('Console is in configuration mode')
            count = len(PAGER.findall(text))
            if count > pages:
                self.sock.sendall(b' ' * (count - pages))
                pages = count
                page_key_at, page_resends = time.monotonic(), 0
                self.progress('CLI pagination: page {}'.format(pages))
        raise TimeoutError('Complete CLI prompt not received before timeout')

    def connect(self):
        deadline = time.monotonic() + self.ready_timeout
        next_notice=0
        while True:
            self._check_cancel()
            if time.monotonic()>=next_notice:
                self.progress('Waiting for console readiness: {}:{} ({}s remaining)'.format(self.host, self.port, max(0, int(deadline - time.monotonic()))))
                next_notice=time.monotonic()+5
            try:
                self.codec = TelnetCodec()
                self.sock = socket.create_connection((self.host, self.port), timeout=min(5, self.ready_timeout))
                self.sock.settimeout(.25)
                self.sock.sendall(b'\r\n')
                _, self.prompt = self._read(min(self.command_timeout, max(.1, deadline - time.monotonic())))
                return
            except PermissionError:
                self.close()
                raise
            except (OSError, TimeoutError):
                self.close()
                self._check_cancel()
                if time.monotonic() >= deadline:
                    raise TimeoutError('Node boot/console readiness timeout')
                time.sleep(.2)

    def command(self, command):
        if '\r' in command or '\n' in command:
            raise ValueError('Multiline commands are not accepted')
        self.awaiting_prompt = True
        self.sock.sendall((command + '\r\n').encode('ascii'))
        text, prompt = self._read(self.command_timeout)
        if prompt != self.prompt:
            raise ValueError('Console prompt changed unexpectedly')
        self.awaiting_prompt = False
        return text

    def restore_terminal_length(self, length):
        """Bounded recovery on this connection, even after a user cancellation."""
        if not isinstance(length, int) or not 0 <= length <= 512:
            raise ValueError('Invalid original terminal length')
        previous_cancel, previous_timeout = self.cancel, self.command_timeout
        self.cancel, self.command_timeout = None, min(3, previous_timeout)
        try:
            if self.awaiting_prompt:
                # Never type a restoration command into an unanswered pager.
                self.sock.sendall(b'\x03')
                _, prompt = self._read(self.command_timeout)
                if prompt != self.prompt:
                    raise ValueError('Cannot resynchronize console for restoration')
                self.awaiting_prompt = False
            return self.command('terminal length {}'.format(length))
        finally:
            self.cancel, self.command_timeout = previous_cancel, previous_timeout

    def enter_enable(self, password=None):
        """One enable attempt; authentication receive bytes are omitted from evidence."""
        if self.prompt.endswith('#'):return True
        if password and (len(password)>256 or any(c in password for c in '\r\n\x00')):
            raise ValueError('Enable password contains unsupported characters')
        start=len(self.wire)
        self.original_prompt=self.prompt
        self.enable_changed=False
        self.enable_pending=True
        try:
            self.awaiting_prompt=True
            self.sock.sendall(b'enable\r\n')
            _,prompt=self._read(min(10,self.command_timeout),allow_password=True)
            if prompt=='<set-password>':
                self.sock.sendall(b'\x03')
                _,prompt=self._read(min(3,self.command_timeout))
                if prompt==self.original_prompt:
                    self.awaiting_prompt=False;self.enable_pending=False
                raise EnableSetupRequired('Device requests creation of a new enable password; not answered')
            if prompt=='<password>':
                self.sock.sendall(((password or '')+'\r\n').encode('utf-8'))
                _,prompt=self._read(min(10,self.command_timeout),allow_password=True)
            # A rejected password is never re-sent. Remaining device attempts are
            # answered with empty input so the CLI returns to its original prompt.
            for _ in range(3):
                if prompt!='<password>':break
                self.sock.sendall(b'\r\n')
                _,prompt=self._read(min(10,self.command_timeout),allow_password=True)
            if prompt=='<password>':
                self.sock.sendall(b'\x03')
                _,prompt=self._read(min(3,self.command_timeout))
            if prompt.endswith('#') and prompt[:-1]==self.original_prompt[:-1]:
                self.prompt=prompt;self.enable_changed=True;self.awaiting_prompt=False
                self.enable_pending=False
                return True
            self.awaiting_prompt=False
            if prompt!=self.original_prompt:
                raise ValueError('Privilege state uncertain after enable attempt')
            self.enable_pending=False
            return False
        finally:
            del self.wire[start:]
            self.authentication_redacted=True

    def restore_privilege(self):
        start=len(self.wire);previous_cancel=self.cancel;self.cancel=None
        try:
            if getattr(self,'enable_pending',False):
                self.sock.sendall(b'\x03')
                end=time.monotonic()+min(3,self.command_timeout)
                _,prompt=self._read(max(.01,end-time.monotonic()),allow_password=True)
                while prompt=='<password>' and time.monotonic()<end:
                    _,prompt=self._read(max(.01,end-time.monotonic()),allow_password=True)
                if prompt not in (self.original_prompt,self.original_prompt[:-1]+'#'):
                    raise ValueError('Original EXEC state could not be established')
                self.prompt=prompt;self.awaiting_prompt=False
                self.enable_changed=prompt.endswith('#');self.enable_pending=False
            self.leave_enable()
        finally:
            self.cancel=previous_cancel
            del self.wire[start:]

    def leave_enable(self):
        if not getattr(self,'enable_changed',False):return
        previous_cancel=self.cancel
        self.cancel=None
        try:
            if self.awaiting_prompt:
                self.sock.sendall(b'\x03')
                _,prompt=self._read(min(3,self.command_timeout))
                if prompt!=self.prompt:raise ValueError('Cannot resynchronize privilege state')
            self.sock.sendall(b'disable\r\n')
            _,prompt=self._read(min(3,self.command_timeout))
            if prompt!=self.original_prompt:raise ValueError('Original privilege prompt not restored')
            self.prompt=prompt;self.awaiting_prompt=False;self.enable_changed=False
        finally:self.cancel=previous_cancel

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None
