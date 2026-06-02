#!/usr/bin/env python3
import json
import mimetypes
import os
import random
import re
import subprocess
import time
import urllib.parse
import urllib.request
import fcntl
from pathlib import Path

ENV_PATH = Path(os.environ.get('VK_ENV_PATH', str(Path.home() / '.hermes/scripts/vk_bridge.env')))
STATE_PATH = Path(os.environ.get('VK_STATE_PATH', str(Path.home() / '.hermes/state/vk_polling_state.json')))
LOG_PATH = Path(os.environ.get('VK_LOG_PATH', str(Path.home() / '.hermes/logs/vk_polling_bridge.log')))
HEARTBEAT_PATH = Path(os.environ.get('VK_HEARTBEAT_PATH', str(Path.home() / '.hermes/state/vk_polling_heartbeat')))
INBOX_DIR = Path(os.environ.get('VK_INBOX_DIR', str(Path.home() / '.hermes/inbox/vk_bridge')))
LOCK_PATH = Path(os.environ.get('VK_LOCK_PATH', '/tmp/vk_polling_bridge.lock'))
HERMES_BIN = os.environ.get('HERMES_BIN', 'hermes')

MAX_VK_MSG = 3500
POLL_SEC = 3
# Research/tool-heavy Hermes runs often exceed 180s (observed: 191s for
# housing-price comparison). Keep VK bridge timeout above the CLI/tool budget so
# valid answers are not killed a few seconds before completion.
HERMES_TIMEOUT_SEC = int(os.environ.get('VK_HERMES_TIMEOUT_SEC', '420'))
# Use one VK Hermes session by default. Earlier quick/full split made Dashboard show
# two VK dialog windows and felt like duplicate conversations. In single-session
# mode every VK request starts with the full tool schema, avoiding toolset snapshot
# problems without creating a second session lane.
VK_FULL_TOOLSETS = os.environ.get(
    'VK_HERMES_FULL_TOOLSETS',
    'web,browser,terminal,file,code_execution,vision,image_gen,tts,skills,todo,memory,session_search,clarify,delegation,cronjob,messaging'
).strip()
VK_SINGLE_SESSION = os.environ.get('VK_HERMES_SINGLE_SESSION', '1').strip().lower() not in ('0', 'false', 'no', 'off')
VK_QUICK_TOOLSETS = os.environ.get('VK_HERMES_QUICK_TOOLSETS', os.environ.get('VK_HERMES_TOOLSETS', 'clarify')).strip()
VK_AUTO_YOLO = os.environ.get('VK_HERMES_AUTO_YOLO', '0').strip().lower() not in ('0', 'false', 'no', 'off')

MEDIA_RE = re.compile(r'(?:MEDIA:|(?:🖼️\s*)?Image:\s*)(/[^\s]+)')
ALLOW_ALL_TRUE = {'1', 'true', 'yes', 'on', 'all'}
APPROVE_RE = re.compile(r'^(?:/approve|approve|/код|код|/code|code)\s+(.+?)\s*$', re.IGNORECASE)


def log(msg):
    ts=time.strftime('%Y-%m-%d %H:%M:%S')
    line=f'[{ts}] {msg}'
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open('a', encoding='utf-8') as f:
        f.write(line+'\n')


def heartbeat():
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_PATH.write_text(str(int(time.time())), encoding='utf-8')


def load_env():
    vals = {}
    if ENV_PATH.exists():
        for raw in ENV_PATH.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    # Environment variables override file values.
    for key, value in os.environ.items():
        if key.startswith('VK_'):
            vals[key] = value
    return vals


def _truthy(value):
    return str(value or '').strip().lower() in ALLOW_ALL_TRUE


def _parse_allowed_users(raw):
    users = set()
    for part in str(raw or '').replace(';', ',').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            users.add(int(part))
        except ValueError:
            log(f'ignoring invalid VK_ALLOWED_USERS entry: {part!r}')
    return users


def is_allowed(env, peer_id, from_id=None, state=None):
    """Default-deny access control for standalone bridge.

    VK community messages can trigger Hermes tool use, including terminal/file
    access in full mode. Therefore a public repo must not default to answering
    every VK user. Set VK_ALLOWED_USERS to comma-separated numeric VK user IDs,
    set VK_APPROVAL_CODE and have users send `/approve <code>`, or explicitly
    set VK_ALLOW_ALL_USERS=1 for public/demo bots.
    """
    if _truthy(env.get('VK_ALLOW_ALL_USERS')):
        return True
    candidates = {int(peer_id)}
    if from_id:
        candidates.add(int(from_id))

    allowed = _parse_allowed_users(env.get('VK_ALLOWED_USERS'))
    if candidates & allowed:
        return True

    approved = set(int(x) for x in (state or {}).get('approved_users', []) if str(x).strip().lstrip('-').isdigit())
    return bool(candidates & approved)


def parse_approval_code(text):
    match = APPROVE_RE.match(text or '')
    return match.group(1).strip() if match else None


def approve_user(state, peer_id, from_id=None):
    approved = set(int(x) for x in state.get('approved_users', []) if str(x).strip().lstrip('-').isdigit())
    approved.add(int(peer_id))
    if from_id:
        approved.add(int(from_id))
    state['approved_users'] = sorted(approved)


def vk_api(method,payload,token):
    data=urllib.parse.urlencode({**payload,'access_token':token,'v':'5.199'}).encode()
    req=urllib.request.Request('https://api.vk.com/method/'+method,data=data,method='POST')
    with urllib.request.urlopen(req,timeout=30) as r:
        body=r.read().decode('utf-8','replace')
    parsed=json.loads(body)
    if 'error' in parsed:
        raise RuntimeError(f"VK API {method}: {parsed['error']}")
    return parsed['response']


def http_post_multipart(url, fields, files):
    boundary = '----hermesvk' + ''.join(random.choice('abcdef0123456789') for _ in range(16))
    chunks = []
    for k, v in fields.items():
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for field, path in files.items():
        path = Path(path)
        ctype = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
        chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'
                       f'Content-Type: {ctype}\r\n\r\n').encode())
        chunks.append(path.read_bytes())
        chunks.append(b'\r\n')
    chunks.append(f'--{boundary}--\r\n'.encode())
    data = b''.join(chunks)
    req = urllib.request.Request(url, data=data, method='POST', headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def split_text(text):
    text=(text or '').strip()
    if not text:
        return ['(пустой ответ)']
    if len(text)<=MAX_VK_MSG:
        return [text]
    out=[]
    rem=text
    while len(rem)>MAX_VK_MSG:
        cut=rem.rfind('\n',0,MAX_VK_MSG)
        if cut<MAX_VK_MSG//2:
            cut=rem.rfind(' ',0,MAX_VK_MSG)
        if cut<MAX_VK_MSG//2:
            cut=MAX_VK_MSG
        out.append(rem[:cut].strip())
        rem=rem[cut:].strip()
    if rem:
        out.append(rem)
    return out


def select_toolsets(text, attachments=None):
    if VK_SINGLE_SESSION:
        return VK_FULL_TOOLSETS, 'single'
    if attachments or wants_full_hermes(text):
        return VK_FULL_TOOLSETS, 'full'
    return VK_QUICK_TOOLSETS, 'quick'


def ask_hermes(peer_id, text, toolsets=None, mode='single'):
    # Keep VK sessions small: daily sessions preserve short context without
    # dragging weeks of transcript into every oneshot call. Default is one VK
    # lane per peer/day, with full tool schema from the start, so Dashboard shows
    # one dialog window and later tool-heavy requests do not inherit a light
    # clarify-only schema.
    mode_suffix = 'vk' if VK_SINGLE_SESSION else ('full' if mode == 'full' else 'quick')
    session=f'vk_{peer_id}_{time.strftime("%Y%m%d")}_{mode_suffix}'
    cmd=[HERMES_BIN]
    if VK_AUTO_YOLO:
        cmd.append('--yolo')
    cmd += ['-c', session]
    if toolsets:
        cmd += ['-t', toolsets]
    cmd += ['-z', text]
    started = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    while True:
        try:
            out, err = proc.communicate(timeout=10)
            break
        except subprocess.TimeoutExpired:
            heartbeat()
            if time.time() - started > HERMES_TIMEOUT_SEC:
                proc.kill()
                out, err = proc.communicate()
                safe_toolsets = toolsets or ''
                log(f'hermes timeout peer={peer_id} session={session} mode={mode} timeout={HERMES_TIMEOUT_SEC}s toolsets={safe_toolsets!r}')
                partial = (out or '').strip()
                if partial:
                    return partial
                return (f'Не успел обработать запрос за {HERMES_TIMEOUT_SEC}с. '
                        'Попробуй сузить запрос или разбить его на части.')
    elapsed = time.time() - started
    log(f'hermes done peer={peer_id} session={session} mode={mode} elapsed={elapsed:.1f}s exit={proc.returncode}')
    out=(out or '').strip()
    if not out:
        out=(err or '').strip()
    return out or '(нет ответа от Hermes)'


def handle_command(text,peer_id):
    t=(text or '').strip().lower()
    if t in ('/help','help'):
        return ('Команды: /help /new /status /trace on /trace off\n'
                'Обычный текст отправляется в Hermes.\n'
                'VK работает в одном диалоговом окне. По умолчанию включён полный набор инструментов, как в Telegram; /trace нужен только для подробного хода глубоких задач.')
    if t in ('/status','status'):
        day = time.strftime("%Y%m%d")
        return (f'VK polling bridge: online\npeer_id: {peer_id}\n'
                f'session: vk_{peer_id}_{day}_vk\n'
                f'toolsets: {VK_FULL_TOOLSETS}\n'
                f'single_session: {VK_SINGLE_SESSION}\n'
                f'auto_yolo: {VK_AUTO_YOLO}\n'
                'routing: single')
    if t in ('/new','new'):
        session_suffixes = ('vk',) if VK_SINGLE_SESSION else ('quick', 'full')
        for suffix in session_suffixes:
            cmd=[HERMES_BIN]
            if VK_AUTO_YOLO:
                cmd.append('--yolo')
            cmd += ['-c',f'vk_{peer_id}_{time.strftime("%Y%m%d")}_{suffix}', '-z','/new']
            subprocess.run(cmd,capture_output=True,text=True,timeout=60)
        return 'Новый контекст VK создан. Можешь писать следующий запрос.'
    return None


def load_state():
    if not STATE_PATH.exists():
        return {'seen_ids': [], 'trace_peers': [], 'approved_users': []}
    try:
        st = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        st.setdefault('seen_ids', [])
        st.setdefault('trace_peers', [])
        st.setdefault('approved_users', [])
        return st
    except Exception:
        return {'seen_ids': [], 'trace_peers': [], 'approved_users': []}


def save_state(st):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding='utf-8')
    tmp.replace(STATE_PATH)


def seen(st,msg_id):
    s=st.get('seen_ids',[])
    if msg_id in s:
        return True
    s.append(msg_id)
    if len(s)>3000:
        s=s[-3000:]
    st['seen_ids']=s
    save_state(st)
    return False


def send_text(token,peer_id,text):
    base=random.randint(1,2_000_000_000)
    for i,part in enumerate(split_text(text)):
        vk_api('messages.send',{
            'peer_id':str(peer_id),
            'random_id':str((base+i)%2147483647),
            'message':part,
        },token)


def upload_photo_attachment(token, peer_id, path):
    server = vk_api('photos.getMessagesUploadServer', {'peer_id': str(peer_id)}, token)
    uploaded = http_post_multipart(server['upload_url'], {}, {'photo': path})
    saved = vk_api('photos.saveMessagesPhoto', {
        'photo': uploaded.get('photo', ''),
        'server': uploaded.get('server', ''),
        'hash': uploaded.get('hash', ''),
    }, token)
    p = saved[0]
    access = ('_' + p['access_key']) if p.get('access_key') else ''
    return f"photo{p['owner_id']}_{p['id']}{access}"


def upload_doc_attachment(token, peer_id, path):
    server = vk_api('docs.getMessagesUploadServer', {'peer_id': str(peer_id)}, token)
    uploaded = http_post_multipart(server['upload_url'], {}, {'file': path})
    saved = vk_api('docs.save', {'file': uploaded.get('file', '')}, token)
    doc = (saved.get('doc') or (saved.get('type') == 'doc' and saved.get('doc')) or saved)[0] if isinstance(saved, list) else saved.get('doc', saved)
    access = ('_' + doc['access_key']) if doc.get('access_key') else ''
    return f"doc{doc['owner_id']}_{doc['id']}{access}"


def upload_attachment(token, peer_id, path):
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(str(path))
    if path.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
        return upload_photo_attachment(token, peer_id, path)
    return upload_doc_attachment(token, peer_id, path)


def send_reply(token, peer_id, reply):
    reply = reply or ''
    media_paths = MEDIA_RE.findall(reply)
    clean = MEDIA_RE.sub('', reply).strip()
    if clean:
        send_text(token, peer_id, clean)
    elif not media_paths:
        send_text(token, peer_id, reply)
    for p in media_paths:
        try:
            att = upload_attachment(token, peer_id, p)
            vk_api('messages.send', {
                'peer_id': str(peer_id),
                'random_id': str(random.randint(1, 2_000_000_000)),
                'attachment': att,
            }, token)
        except Exception as e:
            log(f'media upload failed peer={peer_id} path={p}: {e}')
            send_text(token, peer_id, f'Не смог отправить файл {p}: {e}')


def send_typing(token, peer_id):
    try:
        vk_api('messages.setActivity', {
            'peer_id': str(peer_id),
            'type': 'typing',
        }, token)
    except Exception:
        pass


def mark_read(token, peer_id):
    """Clear VK unread flag after a message is handled.

    The bridge polls `filter=unread`. `seen_ids` prevents duplicate handling, but
    marking dialogs read keeps VK from returning already-processed dialogs forever
    and reduces pointless API work.
    """
    try:
        vk_api('messages.markAsRead', {'peer_id': str(peer_id)}, token)
    except Exception as e:
        log(f'mark_read failed peer={peer_id}: {e}')


def trace_enabled(st, peer_id):
    return int(peer_id) in set(int(x) for x in st.get('trace_peers', []))


def is_deep_task(text):
    t = (text or '').strip().lower()
    if not t:
        return False

    coding_markers = [
        'код', 'програм', 'python', 'js', 'javascript', 'typescript', 'sql', 'bash',
        'docker', 'api', 'backend', 'frontend', 'debug', 'ошибк', 'stack trace',
        'рефактор', 'тест', 'pytest', 'git', 'репозитор', 'функц', 'скрипт',
        'внедри', 'реализ', 'почини', 'исправ', 'архитектур', 'интеграц',
    ]

    # Глубокая задача: длинный технический запрос или явный coding-контекст.
    # Используется только для /trace-подробностей, не для обычного статуса.
    long_request = len(t) >= 160
    coding_context = any(m in t for m in coding_markers)
    structured_request = any(x in t for x in ('```', '1)', '2)', 'шаг', 'требован'))

    return coding_context and (long_request or structured_request)


def wants_service_status(text):
    """Return True when the request is likely to trigger real tool work."""
    t = (text or '').strip().lower()
    if not t:
        return False

    simple_phrases = {
        'привет', 'здравствуй', 'здравствуйте', 'доброе утро', 'добрый день',
        'добрый вечер', 'ку', 'хай', 'hello', 'hi', 'спасибо', 'ок', 'окей',
        'понял', 'ага', 'да', 'нет', '+', '👍', 'как дела?', 'как дела',
    }
    if t in simple_phrases:
        return False

    command_like = t.startswith(('/new', '/help', '/status', '/trace'))
    if command_like:
        return False

    tool_markers = [
        # Search/current facts/news
        'найди', 'найти', 'поищи', 'поиск', 'загугли', 'посмотри в интернете',
        'проверь в интернете', 'проверь сайт', 'ссылка', 'url', 'http://',
        'https://', 'новост', 'актуальн', 'сейчас', 'сегодня', 'курс ',
        'погода', 'цена', 'статус', 'сравни',
        # Coding/debugging/files/system
        'код', 'скрипт', 'програм', 'python', 'javascript', 'typescript', 'sql',
        'bash', 'docker', 'api', 'backend', 'frontend', 'git', 'github',
        'репозитор', 'debug', 'ошибк', 'traceback', 'stack trace', 'pytest',
        'тест', 'рефактор', 'почини', 'исправ', 'реализ', 'внедри', 'установ',
        'настрой', 'интеграц', 'файл', 'папк', 'лог', 'порт', 'процесс',
        # Hermes/productivity tools
        'календар', 'gmail', 'почт', 'письм', 'kanban', 'канбан', 'задач',
        'напомни', 'создай', 'запусти', 'сделай',
        # Natural Russian install/action wording not covered by stems above.
        'поставь', 'поставим', 'поставить', 'поставил', 'установи', 'инсталл',
    ]
    if any(m in t for m in tool_markers):
        return True

    structured_request = any(x in t for x in ('```', '1)', '2)', 'шаг', 'требован'))
    long_action_request = len(t) >= 140 and any(v in t for v in (
        'сделай', 'разбер', 'проанализ', 'сравни', 'подготов', 'составь'
    ))
    return structured_request or long_action_request


def wants_full_hermes(text):
    return wants_service_status(text) or is_deep_task(text)


def safe_name(value, fallback):
    value = re.sub(r'[^A-Za-z0-9._-]+', '_', str(value or '')).strip('._')
    return value or fallback


def download_url(url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={'User-Agent': 'Hermes-VK-Bridge/1.0'})
    with urllib.request.urlopen(req, timeout=120) as r:
        path.write_bytes(r.read())
    return path


def collect_attachments(msg):
    out = []
    for idx, att in enumerate(msg.get('attachments') or []):
        typ = att.get('type') or 'unknown'
        obj = att.get(typ) or {}
        url = None
        ext = ''
        title = obj.get('title') or obj.get('text') or ''
        if typ == 'photo':
            sizes = obj.get('sizes') or []
            best = max(sizes, key=lambda s: int(s.get('width', 0))*int(s.get('height', 0)), default={})
            url = best.get('url')
            ext = '.jpg'
        elif typ == 'doc':
            url = obj.get('url')
            ext = Path(urllib.parse.urlparse(url or '').path).suffix or Path(obj.get('title') or '').suffix or '.bin'
        elif typ == 'audio_message':
            url = obj.get('link_ogg') or obj.get('link_mp3')
            ext = '.ogg' if obj.get('link_ogg') else '.mp3'
        elif typ in ('video', 'audio'):
            # VK often does not expose direct playable URLs for these in bot API.
            out.append({'type': typ, 'path': None, 'title': title, 'note': 'direct download URL unavailable'})
            continue

        if not url:
            out.append({'type': typ, 'path': None, 'title': title, 'note': 'no direct URL'})
            continue
        try:
            name = safe_name(title, f'{typ}_{int(time.time())}_{idx}')
            if not Path(name).suffix:
                name += ext or '.bin'
            path = INBOX_DIR / time.strftime('%Y%m%d') / name
            # Avoid collisions when VK sends repeated generic titles.
            if path.exists():
                path = path.with_name(f'{path.stem}_{random.randint(1000,9999)}{path.suffix}')
            download_url(url, path)
            out.append({'type': typ, 'path': str(path), 'title': title, 'note': ''})
        except Exception as e:
            log(f'attachment download failed type={typ}: {e}')
            out.append({'type': typ, 'path': None, 'title': title, 'note': f'download failed: {e}'})
    return out


def build_prompt(text, attachments):
    text = (text or '').strip()
    if not attachments:
        return text
    lines = []
    if text:
        lines += ['Текст сообщения из VK:', text, '']
    else:
        lines += ['Пользователь прислал вложение/вложения без текста.', '']
    lines.append('Вложения VK сохранены локально:')
    for a in attachments:
        if a.get('path'):
            lines.append(f"- {a.get('type')}: {a.get('path')}")
        else:
            lines.append(f"- {a.get('type')}: недоступно ({a.get('note')})")
    lines += ['', 'Обработай вложения как в Telegram: если это фото/скрин — проанализируй изображение через vision; если документ — прочитай/извлеки содержимое; если голосовое — попробуй транскрибировать или честно скажи, если текущих инструментов недостаточно. Ответь пользователю по-русски.']
    return '\n'.join(lines)


def main():
    lock_f = LOCK_PATH.open('w')
    try:
        fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log('another VK polling bridge is already running; exiting')
        return

    env=load_env()
    token=env.get('VK_GROUP_TOKEN', '')
    if not token:
        raise RuntimeError(f'VK_GROUP_TOKEN is missing. Set it in env or {ENV_PATH}')
    st=load_state()
    log('VK polling bridge started')
    while True:
        heartbeat()
        try:
            resp=vk_api('messages.getConversations',{'count':'20','filter':'unread'},token)
            for item in resp.get('items',[]):
                msg=item.get('last_message',{})
                if msg.get('out')==1:
                    continue
                msg_id=int(msg.get('id') or 0)
                if msg_id<=0 or seen(st,msg_id):
                    continue
                peer_id=int(msg.get('peer_id') or 0)
                from_id=int(msg.get('from_id') or peer_id or 0)
                text=(msg.get('text') or '').strip()
                if peer_id<=0:
                    continue
                raw_cmd = (text or '').strip().lower()
                submitted_code = parse_approval_code(text)
                if not is_allowed(env, peer_id, from_id, st):
                    expected_code = (env.get('VK_APPROVAL_CODE') or '').strip()
                    if submitted_code and expected_code and submitted_code == expected_code:
                        approve_user(st, peer_id, from_id)
                        save_state(st)
                        send_text(token, peer_id, '✅ Доступ одобрен. Теперь можно писать запросы Hermes.')
                    elif submitted_code:
                        send_text(token, peer_id, '❌ Неверный код доступа.')
                    elif expected_code:
                        send_text(token, peer_id, '🔒 Доступ закрыт. Отправь: /approve <код доступа>.')
                    else:
                        send_text(token, peer_id, '🔒 Доступ закрыт. Администратор должен добавить твой VK ID в VK_ALLOWED_USERS.')
                    log(f'denied peer={peer_id} from={from_id} msg_id={msg_id}: not approved')
                    mark_read(token, peer_id)
                    continue
                if raw_cmd in ('/trace on', 'trace on'):
                    peers = set(int(x) for x in st.get('trace_peers', []))
                    peers.add(peer_id)
                    st['trace_peers'] = sorted(peers)
                    save_state(st)
                    send_text(token, peer_id, '✅ Трассировка включена. Команды: /trace off для отключения.')
                    continue
                if raw_cmd in ('/trace off', 'trace off'):
                    peers = set(int(x) for x in st.get('trace_peers', []))
                    peers.discard(peer_id)
                    st['trace_peers'] = sorted(peers)
                    save_state(st)
                    send_text(token, peer_id, '🛑 Трассировка выключена.')
                    continue

                attachments = collect_attachments(msg)
                prompt = build_prompt(text, attachments)
                toolsets, mode = select_toolsets(text, attachments)
                trace_now = trace_enabled(st, peer_id) or is_deep_task(text)

                if trace_now:
                    preview = text[:120] if text else f'{len(attachments)} вложений'
                    send_text(token, peer_id, f'📥 Получил: {preview}')
                    send_text(token, peer_id, f'🧰 Режим: {mode}')
                send_typing(token, peer_id)
                started = time.time()
                if trace_now:
                    send_text(token, peer_id, '🧠 Обрабатываю запрос...')

                reply=handle_command(text,peer_id)
                if reply is None:
                    try:
                        reply=ask_hermes(peer_id,prompt,toolsets=toolsets,mode=mode)
                    except Exception as e:
                        reply=f'Ошибка обработки: {e}'

                send_reply(token,peer_id,reply)
                elapsed = time.time() - started
                if trace_now:
                    send_text(token, peer_id, f'✅ Готово за {elapsed:.1f}с')
                mark_read(token, peer_id)
                log(f'replied peer={peer_id} msg_id={msg_id} mode={mode} attachments={len(attachments)} len={len(text)}')
        except Exception as e:
            log(f'loop error: {e}')
        time.sleep(POLL_SEC)


if __name__=='__main__':
    main()
