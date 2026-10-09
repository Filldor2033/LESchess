#!/usr/bin/env python3
"""LESchess — локальные веб-шахматы с чатом и комнатами. Один файл + python-chess."""
import asyncio, json, time, secrets
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import websockets
import chess

PIECE_VAL = {'K': 0, 'Q': 9, 'R': 5, 'B': 3, 'N': 3, 'P': 1}

# python-chess: rank 0 = 1-я горизонталь. Наш UI: y=0 сверху = 8-я горизонталь.
# перевод: rank = 7 - y, file = x
class Game:
    def __init__(self, move_time=None):
        self.b = chess.Board()
        self.moves = 0
        self.history = []    # стек сделанных (sx, sy, tx, ty) для отмены
        self.move_time = move_time    # сек на ход; None = без лимита
        self.deadline = None          # unix ts, до какого времени текущий игрок должен ходить

    def piece(self, x, y):
        p = self.b.piece_at(chess.square(x, 7 - y))
        if p is None: return None
        return ('w' if p.color == chess.WHITE else 'b') + p.symbol().upper()

    def board_grid(self):
        return [[self.piece(x, y) for x in range(8)] for y in range(8)]

    def legal_moves(self, x, y):
        sq = chess.square(x, 7 - y)
        if self.b.piece_at(sq) is None: return []
        return [(chess.square_file(mv.to_square), 7 - chess.square_rank(mv.to_square))
                for mv in self.b.legal_moves if mv.from_square == sq]

    def apply_move(self, sx, sy, tx, ty, promote=None):
        sq_from = chess.square(sx, 7 - sy)
        sq_to = chess.square(tx, 7 - ty)
        piece = self.b.piece_at(sq_from)
        mv = None
        if (piece and piece.symbol().upper() == 'P'
                and chess.square_rank(sq_to) in (0, 7)):
            # запрос промоции: Q/R/B/N (по умолчанию Q)
            promo = (promote or 'Q').upper()
            if promo not in ('Q', 'R', 'B', 'N'):
                promo = 'Q'
            mv = chess.Move(sq_from, sq_to, promotion=chess.Piece.from_symbol(promo).piece_type)
            if mv not in self.b.legal_moves:
                mv = chess.Move(sq_from, sq_to, promotion=chess.QUEEN)
        else:
            mv = chess.Move(sq_from, sq_to)
        if mv not in self.b.legal_moves: return None
        captured = self.piece(tx, ty)
        self.b.push(mv)
        self.moves += 1
        self.history.append((sx, sy, tx, ty, mv.promotion))
        self.restart_clock()
        return {'captured': captured}

    def restart_clock(self):
        """Запустить/перезапустить отсчёт на ход текущего игрока."""
        if self.move_time and self.status() == 'active' and not self.deadline:
            self.deadline = now() + self.move_time
        elif self.move_time:
            self.deadline = now() + self.move_time

    def undo(self, halfmoves=2):
        """Откатить n полуходов (по умолчанию пара = один полный ход)."""
        if halfmoves > len(self.history):
            halfmoves = len(self.history)
        if halfmoves <= 0: return False
        for _ in range(halfmoves):
            self.b.pop()
            self.history.pop()
            self.moves = max(0, self.moves - 1)
        # после отката снова даём текущему игроку полный лимит
        if self.move_time and self.status() == 'active':
            self.deadline = now() + self.move_time
        return True

    def status(self):
        if self.b.is_checkmate(): return 'checkmate'
        if self.b.is_stalemate(): return 'stalemate'
        if getattr(self, 'resigned', None): return 'resign'
        if getattr(self, 'draw_agreed', False): return 'draw'
        if getattr(self, 'flag_fell', None): return 'timeup'
        if self.b.is_insufficient_material(): return 'draw'
        if self.b.is_fifty_moves(): return 'draw'
        if self.b.is_repetition(3): return 'draw'
        return 'active'

    def result(self):
        if self.b.is_checkmate():
            return 'w' if self.b.turn == chess.BLACK else 'b'
        if getattr(self, 'resigned', None):
            return 'b' if self.resigned == 'w' else 'w'   # победил не сдавшийся
        if getattr(self, 'flag_fell', None):
            return 'b' if self.flag_fell == 'w' else 'w'  # победил не просрочивший
        return 'draw'

    def in_check(self):
        return self.b.is_check()

    def turn_color(self):
        return 'w' if self.b.turn == chess.WHITE else 'b'

    def material(self):
        d = 0
        for sq in chess.SQUARES:
            p = self.b.piece_at(sq)
            if p:
                v = PIECE_VAL[p.symbol().upper()]
                d += v if p.color == chess.WHITE else -v
        return d

    def san_history(self):
        """История ходов в шахматной нотации (SAN), парами: [белые, чёрные, ...]."""
        # восстановить SAN: replay из начальной позиции
        tmp = chess.Board()
        sans = []
        for mv in self.b.move_stack:
            try:
                sans.append(tmp.san(mv))
                tmp.push(mv)
            except Exception:
                break
        # сгруппировать парами
        pairs = []
        for i in range(0, len(sans), 2):
            pairs.append([sans[i], sans[i + 1] if i + 1 < len(sans) else None])
        return pairs

    def state(self):
        st = self.status()
        left = max(0, int(self.deadline - now())) if self.deadline else None
        return {'board': self.board_grid(), 'turn': self.turn_color(),
                'status': st,
                'result': self.result() if st in ('checkmate', 'stalemate', 'draw', 'resign', 'timeup') else None,
                'moves': self.moves,
                'inCheck': self.b.is_check() if st == 'active' else False,
                'material': self.material(),
                'san': self.san_history(),
                'moveTime': self.move_time, 'timeLeft': left}


# ---------------- Бот (Stockfish) ----------------

import chess.engine

BOT_NAME = 'Компьютер'
STOCKFISH_BIN = None   # автопоиск при старте
_sf_engine = None      # ленивый общий инстанс

def _find_stockfish():
    """Найти бинарь Stockfish в типовых местах."""
    import shutil, os
    for path in (shutil.which('stockfish'), '/usr/games/stockfish', '/usr/bin/stockfish',
                 '/usr/local/bin/stockfish'):
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None

# уровни сложности: имя -> (UCI_Elo, Skill Level, сек на ход)
BOT_LEVELS = {
    'easy':   ('Лёгкий',   1350,  2, 0.05),
    'normal': ('Средний',  1700,  8, 0.1),
    'hard':   ('Сильный',  2200, 14, 0.2),
    'master': ('Мастер',   3000, 20, 0.4),
}

def bot_display_name(level):
    """Имя бота с уровнем: «Компьютер (Средний)»."""
    return f'{BOT_NAME} ({BOT_LEVELS.get(level, BOT_LEVELS["normal"])[0]})'

def sf_get(level=None):
    """ОДИН общий движок (уровень задаётся per-request в play())."""
    global _sf_engine, STOCKFISH_BIN
    if _sf_engine is not None:
        # проверка живости: мёртвый процесс — пересоздаём
        try:
            if _sf_engine.ping():
                return _sf_engine
        except Exception:
            pass
        try:
            _sf_engine.quit()
        except Exception:
            pass
        _sf_engine = None
    STOCKFISH_BIN = STOCKFISH_BIN or _find_stockfish()
    if not STOCKFISH_BIN:
        return None
    try:
        _sf_engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_BIN)
        _sf_engine.configure({'Threads': 1, 'Hash': 64})
        return _sf_engine
    except Exception:
        _sf_engine = None
        return None

def bot_think_time(level):
    return BOT_LEVELS.get(level, BOT_LEVELS['normal'])[3]

def bot_options(level):
    """UCI-опции уровня для передачи в engine.play()."""
    if level not in BOT_LEVELS:
        level = 'normal'
    _, elo, skill, _t = BOT_LEVELS[level]
    return {'UCI_Elo': elo, 'Skill Level': skill}

async def bot_play_if_turn(r):
    """Если очередь бота — Stockfish выбирает ход (уровень сложности комнаты)."""
    g = r['game']
    if g.status() != 'active': return
    bot_color = r['bot_color']
    if g.turn_color() != bot_color: return
    level = r.get('bot_level') or 'normal'
    eng = sf_get(level)
    if eng is None:
        return   # движка нет — бот молчит (клиент покажет отсутствие хода)
    try:
        t = bot_think_time(level)
        opts = bot_options(level)
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: eng.play(g.b.copy(), chess.engine.Limit(time=t), options=opts))
        mv = result.move
    except Exception:
        return
    if mv is None: return
    sx, sy = chess.square_file(mv.from_square), 7 - chess.square_rank(mv.from_square)
    tx, ty = chess.square_file(mv.to_square), 7 - chess.square_rank(mv.to_square)
    res = g.apply_move(sx, sy, tx, ty, promote=(chess.piece_symbol(mv.promotion).upper() if mv.promotion else None))
    if res is None: return
    await broadcast(r, {'type': 'move', 'bot': True, 'from': [sx, sy], 'to': [tx, ty],
                        'piece': res['captured'] and None, 'state': g.state()})
    if g.status() != 'active':
        notify_lobby()

# ---------------- Комнаты и WS ----------------

ROOMS = {}      # room_id -> {'id','name','password','created','game','clients':{ws:name},'chat':[],'players':{...}}
LOBBY = set()   # соединения, смотрящие список комнат (ещё не в комнате)
CONNS = set()

def now(): return int(time.time())

def room_public(r):
    # бот занимает второй слот — комната выглядит полной (2/2)
    players = len(r['clients']) + (1 if r.get('bot_color') else 0)
    return {'id': r['id'], 'name': r['name'], 'players': players,
            'status': r['game'].status(), 'created': r['created'],
            'locked': bool(r.get('password')),
            'bot': bool(r.get('bot_color')),
            'botLevel': r.get('bot_level') or 'normal',
            'moveTime': r['game'].move_time}

def notify_lobby():
    """Сообщить лобби обновлённый список комнат (неблокирующе)."""
    obj = json.dumps({'type': 'rooms', 'rooms': [room_public(r) for r in ROOMS.values()]},
                     ensure_ascii=False)
    for ws in list(LOBBY):
        try:
            asyncio.ensure_future(ws.send(obj))
        except Exception:
            LOBBY.discard(ws)

async def broadcast(r, obj, exclude=None):
    dead = []
    for ws in list(r['clients'].keys()):
        if ws is exclude: continue
        try:
            await ws.send(json.dumps(obj, ensure_ascii=False))
        except Exception:
            dead.append(ws)
    for ws in dead: leave_room(r, ws)

def leave_room(r, ws):
    name = r['clients'].pop(ws, None)
    if name is None: return
    if r['players'].get('w') == name: r['players']['w'] = None
    if r['players'].get('b') == name: r['players']['b'] = None
    broadcast_sync(r, {'type': 'system', 'text': f'{name} покинул комнату'})
    broadcast_sync(r, {'type': 'room', 'room': room_public(r)})
    broadcast_sync(r, {'type': 'players', 'players': r['players']})
    if not r['clients']:
        ROOMS.pop(r['id'], None)   # пустая комната удаляется
    notify_lobby()

def broadcast_sync(r, obj):
    asyncio.ensure_future(broadcast(r, obj))

async def enter_room(ws, r, name, is_bot=False):
    """Поместить соединение в комнату: дедуп имени, цвет, welcome, нотификации."""
    n = 1
    base = name
    while name in r['clients'].values():
        n += 1; name = f'{base} {n}'
    r['clients'][ws] = name
    if is_bot:
        # бот занимает противоположный слот от человека
        human_color = 'b' if r['bot_color'] == 'w' else 'w'
        r['players'][r['bot_color']] = name
    elif r['players']['w'] is None: r['players']['w'] = name
    elif r['players']['b'] is None: r['players']['b'] = name
    LOBBY.discard(ws)
    # оба на месте и лимит задан — запускаем часы
    g = r['game']
    if g.move_time and r['players']['w'] and r['players']['b'] and not g.deadline and g.status() == 'active':
        g.deadline = now() + g.move_time
    await ws.send(json.dumps({'type': 'welcome', 'name': name, 'color': 'w' if r['players']['w']==name else 'b',
                              'room': room_public(r), 'state': r['game'].state(),
                              'players': r['players'], 'chat': r['chat'][-60:]}, ensure_ascii=False))
    await broadcast(r, {'type': 'system', 'text': f'{name} присоединился'}, exclude=ws)
    broadcast_sync(r, {'type': 'room', 'room': room_public(r)})
    broadcast_sync(r, {'type': 'players', 'players': r['players']})
    notify_lobby()
    # бот играет за свой цвет (с задержкой)
    if is_bot:
        asyncio.ensure_future(bot_turn_soon(r))
    return name

async def bot_turn_soon(r, delay=1.2):
    """Бот думает непродолжительное время и ходит, если его очередь."""
    await asyncio.sleep(delay)
    try:
        await bot_play_if_turn(r)
    except Exception:
        pass

async def try_premove(room):
    """Если игрок заготовил ход наперёд и пришла его очередь — применить его.
    Нелегальный/устаревший ход отменяется с уведомлением игрока."""
    pm = room.get('premove')
    if not pm: return
    g = room['game']
    if g.status() != 'active':
        room['premove'] = None
        return
    if g.turn_color() != pm['color']:
        return   # ещё не очередь — ждём
    room['premove'] = None
    sx, sy = pm['from']
    tx, ty = pm['to']
    if (tx, ty) not in g.legal_moves(sx, sy):
        # ход стал нелегальным (позиция изменилась) — отмена
        for ws, nm in list(room['clients'].items()):
            if nm == pm['player']:
                try:
                    await ws.send(json.dumps({'type': 'premove', 'ok': False,
                                              'text': 'Заготовленный ход стал невозможен'}, ensure_ascii=False))
                except Exception:
                    pass
                break
        return
    res = g.apply_move(sx, sy, tx, ty, promote=pm.get('promote'))
    if res is None: return
    await broadcast(room, {'type': 'move', 'from': [sx, sy], 'to': [tx, ty],
                           'premoved': True, 'piece': res['captured'] and None,
                           'state': g.state()})
    if g.status() != 'active':
        notify_lobby()


async def handler(ws):
    name = None
    room = None
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            t = msg.get('type')
            if t == 'join':
                if room: continue
                name = (msg.get('name') or 'Гость').strip()[:20] or 'Гость'
                room_id = msg.get('room') or ('r-' + secrets.token_hex(4))
                password = (msg.get('password') or '').strip()[:40]
                r = ROOMS.get(room_id)
                if not r:
                    # создать, если не существует (reconnect или прямой вход)
                    r = {'id': room_id, 'name': (msg.get('roomName') or room_id).strip()[:40] or room_id,
                         'password': password, 'created': now(),
                         'game': Game(), 'clients': {}, 'chat': [], 'players': {'w': None, 'b': None},
                         'requests': {}}
                    ROOMS[room_id] = r
                elif r.get('password') and r['password'] != password:
                    await ws.send(json.dumps({'type': 'error', 'text': 'Неверный пароль комнаты'}, ensure_ascii=False))
                    continue
                room = r
                r.setdefault('premove', None)
                r.setdefault('bot_color', None)
                if r.get('bot_color') and len(r['clients']) >= 1:
                    await ws.send(json.dumps({'type': 'error', 'text': 'В комнате играет компьютер — комната закрыта'}, ensure_ascii=False))
                    room = None
                    continue
                if len(r['clients']) >= 2:
                    await ws.send(json.dumps({'type': 'error', 'text': 'Комната заполнена (макс. 2 игрока)'}, ensure_ascii=False))
                    room = None
                    continue
                name = await enter_room(ws, r, name)
            elif t == 'create' and not room:
                # создать комнату со своим названием, паролем и лимитом времени на ход
                rname = (msg.get('name') or '').strip()[:40]
                password = (msg.get('password') or '').strip()[:40]
                if not rname:
                    await ws.send(json.dumps({'type': 'error', 'text': 'Придумай название комнаты'}, ensure_ascii=False))
                    continue
                # лимит: 5..600 сек или без него (0)
                try:
                    move_time = int(msg.get('moveTime') or 0)
                except Exception:
                    move_time = 0
                if move_time != 0 and (move_time < 5 or move_time > 600): move_time = 0
                game = Game(move_time=move_time or None)
                # часы стартуют, когда сели оба
                game.deadline = None
                name = (msg.get('playerName') or localStorage_name(msg) or 'Гость')
                name = (name or 'Гость').strip()[:20] or 'Гость'
                room_id = 'r-' + secrets.token_hex(4)
                r = {'id': room_id, 'name': rname, 'password': password, 'created': now(),
                     'game': game, 'clients': {}, 'chat': [], 'players': {'w': None, 'b': None},
                     'requests': {}, 'premove': None, 'bot_color': None}
                ROOMS[room_id] = r
                room = r
                name = await enter_room(ws, r, name)
            elif t == 'premove' and room:
                # ход наперёд: игрок (не в свою очередь) заранее показывает ход;
                # применится автоматически, когда придёт его очередь и ход легален
                g = room['game']
                if g.status() != 'active':
                    await ws.send(json.dumps({'type': 'premove', 'ok': False, 'text': 'Партия окончена'}, ensure_ascii=False))
                    continue
                mycolor = 'w' if room['players'].get('w') == name else 'b'
                if g.turn_color() == mycolor:
                    await ws.send(json.dumps({'type': 'premove', 'ok': False, 'text': 'Сейчас твой ход — ходи обычным ходом'}, ensure_ascii=False))
                    continue
                if msg.get('clear'):
                    room['premove'] = None
                    await ws.send(json.dumps({'type': 'premove', 'ok': True, 'cleared': True}))
                    continue
                try:
                    frm, to = msg['from'], msg['to']
                except Exception:
                    continue
                # фигура на поле должна быть своя
                p = g.piece(frm[0], frm[1])
                if not p or p[0] != mycolor:
                    await ws.send(json.dumps({'type': 'premove', 'ok': False, 'text': 'Это не твоя фигура'}, ensure_ascii=False))
                    continue
                room['premove'] = {'player': name, 'color': mycolor,
                                   'from': [frm[0], frm[1]], 'to': [to[0], to[1]],
                                   'promote': msg.get('promote') or None}
                await ws.send(json.dumps({'type': 'premove', 'ok': True, 'from': [frm[0], frm[1]], 'to': [to[0], to[1]]}))

            elif t == 'bot' and room:
                # позвать бота в комнату / выгнать его
                if msg.get('kick'):
                    if not room.get('bot_color'):
                        await ws.send(json.dumps({'type': 'error', 'text': 'В комнате нет компьютера'}, ensure_ascii=False))
                        continue
                    bc = room['bot_color']
                    bname = room['players'].get(bc)
                    room['bot_color'] = None
                    room['bot_level'] = None
                    # бот «выходит»: чистим слот
                    if room['players'].get(bc) == bname:
                        room['players'][bc] = None
                    # если шла партия — она окончена сдачей бота
                    g = room['game']
                    if g.status() == 'active' and g.moves > 0:
                        g.resigned = bc
                        g.deadline = None
                        await broadcast(room, {'type': 'system', 'text': 'Компьютер выключен — партия прервана', 'state': g.state()})
                    else:
                        await broadcast(room, {'type': 'system', 'text': 'Компьютер покинул комнату', 'state': g.state()})
                    broadcast_sync(room, {'type': 'room', 'room': room_public(room)})
                    broadcast_sync(room, {'type': 'players', 'players': room['players']})
                    notify_lobby()
                    continue
                # позвать: только если в комнате 1 человек и бот не сидит
                if room.get('bot_color'):
                    await ws.send(json.dumps({'type': 'error', 'text': 'Компьютер уже в комнате'}, ensure_ascii=False))
                    continue
                if len(room['clients']) >= 2:
                    await ws.send(json.dumps({'type': 'error', 'text': 'Комната занята двумя игроками'}, ensure_ascii=False))
                    continue
                # цвет бота: противоположный единственному человеку
                human_color = 'w' if room['players'].get('w') else 'b'
                if not room['players'].get('w') and not room['players'].get('b'):
                    await ws.send(json.dumps({'type': 'error', 'text': 'Ждём второго игрока или позови компьютер', 'sorted': True}, ensure_ascii=False))
                    continue
                room['bot_color'] = 'b' if human_color == 'w' else 'w'
                # уровень сложности
                level = msg.get('level') or 'normal'
                if level not in BOT_LEVELS: level = 'normal'
                room['bot_level'] = level
                # бот «заходит» как виртуальный игрок
                bc = room['bot_color']
                lvl_name = BOT_LEVELS[level][0]
                room['players'][bc] = f'{BOT_NAME} ({lvl_name})'
                g = room['game']
                # бот чёрный? он стартует только после хода белых
                await broadcast(room, {'type': 'system', 'text': 'Компьютер присоединился к партии', 'state': g.state()})
                broadcast_sync(room, {'type': 'players', 'players': room['players']})
                broadcast_sync(room, {'type': 'room', 'room': room_public(r=room)})
                notify_lobby()
                # если бот белые — стартует часы и ход
                if g.move_time and room['players']['w'] and room['players']['b'] and g.status() == 'active' and not g.deadline:
                    g.deadline = now() + g.move_time
                asyncio.ensure_future(bot_turn_soon(room, delay=0.8))

            elif t == 'leave' and room:
                # явный выход: сразу освобождаем место и слот цвета
                if room.get('premove', {}).get('player') == name if room.get('premove') else False:
                    room['premove'] = None
                leave_room(room, ws)
                room = None

            elif t == 'settime' and room:
                # сменить лимит времени на ход в живой комнате
                try:
                    mt = int(msg.get('moveTime') or 0)
                except Exception:
                    mt = -1
                if mt != 0 and (mt < 5 or mt > 600):
                    await ws.send(json.dumps({'type': 'error', 'text': 'Лимит: 0 или 5–600 секунд'}, ensure_ascii=False))
                    continue
                g = room['game']
                if g.status() != 'active':
                    await ws.send(json.dumps({'type': 'error', 'text': 'Партия уже окончена'}, ensure_ascii=False))
                    continue
                if g.moves > 0:
                    await ws.send(json.dumps({'type': 'error', 'text': 'Время можно менять только до начала партии'}, ensure_ascii=False))
                    continue
                g.move_time = mt or None
                if g.move_time:
                    g.deadline = now() + g.move_time   # часы с новым лимитом сразу
                else:
                    g.deadline = None                  # без лимита — часы прочь
                g.flag_fell = None
                await broadcast(room, {'type': 'system',
                                       'text': ('Лимит времени: ' + str(mt) + ' сек на ход') if mt else 'Лимит времени снят',
                                       'state': g.state()})
                notify_lobby()

            elif t == 'list' and not room:
                # подписка на список комнат (лобби)
                LOBBY.add(ws)
                await ws.send(json.dumps({'type': 'rooms', 'rooms': [room_public(r) for r in ROOMS.values()]},
                                         ensure_ascii=False))
            elif t == 'chat' and room:
                text = (msg.get('text') or '').strip()[:500]
                if not text: continue
                cmsg = {'type': 'chat', 'name': name, 'text': text, 'ts': now()}
                room['chat'].append(cmsg)
                if len(room['chat']) > 200: room['chat'] = room['chat'][-200:]
                await broadcast(room, cmsg)
            elif t == 'move' and room:
                g = room['game']
                mycolor = 'w' if room['players'].get('w') == name else 'b'
                if g.status() != 'active' or g.turn_color() != mycolor:
                    await ws.send(json.dumps({'type': 'error', 'text': 'Не ваш ход'}))
                    continue
                try:
                    sx, sy, tx, ty = msg['from'][0], msg['from'][1], msg['to'][0], msg['to'][1]
                except Exception:
                    continue
                if (tx, ty) not in g.legal_moves(sx, sy):
                    await ws.send(json.dumps({'type': 'error', 'text': 'Недопустимый ход'}))
                    continue
                res = g.apply_move(sx, sy, tx, ty, promote=(msg.get('promote') or None))
                await broadcast(room, {'type': 'move', 'from': [sx, sy], 'to': [tx, ty],
                                       'piece': res['captured'] and None, 'state': g.state()})
                if g.status() != 'active':
                    notify_lobby()   # комната «в игре» -> «свободна/окончена» в лобби
                await try_premove(room)
                # очередь бота — он думает и ходит
                if room.get('bot_color') and g.status() == 'active':
                    asyncio.ensure_future(bot_turn_soon(room))

            # (авто-ход наперёд вынесен в хелпер ниже)
            elif t == 'reset' and room:
                alone = not room['players'].get('w') or not room['players'].get('b')
                if alone and not room.get('bot_color'):
                    # один человек без бота: пересоздать партию можно, стороны не трогаем
                    room['game'] = Game(move_time=room['game'].move_time)
                    room['requests'].clear()
                    room['premove'] = None
                    await broadcast(room, {'type': 'system', 'text': 'Новая партия', 'state': room['game'].state()})
                    notify_lobby()
                    continue
                if alone:
                    await ws.send(json.dumps({'type': 'error', 'text': 'Ждём второго игрока'}, ensure_ascii=False))
                    continue
                # смена сторон: кто играл белыми — теперь чёрными
                # бот при этом меняет цвет вместе с человеком? НЕТ: у бота фиксированный
                # слот bot_color — человек меняется с ботом местами
                bc = room.get('bot_color')
                if bc:
                    # человек и бот меняются цветами
                    room['bot_color'] = 'w' if bc == 'b' else 'b'
                    bc = room['bot_color']
                    room['players']['w'], room['players']['b'] = room['players']['b'], room['players']['w']
                    # восстановить имена на слотах после swap
                    room['players'][bc] = bot_display_name(room.get('bot_level'))
                    human_slot = 'w' if bc == 'b' else 'b'
                    human_names = [nm for nm in room['players'].values() if nm and nm != BOT_NAME]
                    # человек теперь на противоположном слоте
                    hn = [nm for ws, nm in room['clients'].items() if ws is not None][0] if room['clients'] else None
                    room['players'][human_slot] = hn
                else:
                    room['players']['w'], room['players']['b'] = room['players']['b'], room['players']['w']
                ng = Game(move_time=room['game'].move_time)
                if ng.move_time and room['players']['w'] and room['players']['b']:
                    ng.deadline = now() + ng.move_time
                room['game'] = ng
                room['requests'].clear()
                room['premove'] = None
                await broadcast(room, {'type': 'system', 'text': 'Новая партия — стороны поменялись', 'state': room['game'].state()})
                broadcast_sync(room, {'type': 'players', 'players': room['players']})
                notify_lobby()
                # бот теперь возможно белые — пусть начинает
                if room.get('bot_color'):
                    asyncio.ensure_future(bot_turn_soon(room, delay=1.0))
            elif t == 'moves' and room:
                mv = room['game'].legal_moves(msg['x'], msg['y'])
                await ws.send(json.dumps({'type': 'moves', 'x': msg['x'], 'y': msg['y'], 'moves': [list(m) for m in mv]}))

            # ---------- Запросы между игроками: отмена хода / ничья / сдача ----------
            elif t in ('undo', 'draw') and room:
                g = room['game']
                if g.status() != 'active':
                    await ws.send(json.dumps({'type': 'error', 'text': 'Партия уже окончена'}, ensure_ascii=False))
                    continue
                # против компьютера: бот сам решает
                bc = room.get('bot_color')
                if bc:
                    if t == 'undo':
                        # бот соглашается на любую отмену
                        if g.moves >= 2:
                            g.undo(2)
                            room['premove'] = None
                            await broadcast(room, {'type': 'system', 'text': 'Компьютер согласился отменить ход', 'state': g.state()})
                        elif g.moves == 1:
                            g.undo(1)
                            room['premove'] = None
                            await broadcast(room, {'type': 'system', 'text': 'Компьютер согласился отменить ход', 'state': g.state()})
                        else:
                            await ws.send(json.dumps({'type': 'error', 'text': 'Ходов ещё не было'}, ensure_ascii=False))
                        continue
                    else:
                        # ничьей с компьютером нет (кнопка скрыта)
                        await ws.send(json.dumps({'type': 'request_done', 'kind': 'draw', 'ok': False, 'text': 'С компьютером ничьей не бывает'}))
                        continue
                other = room['players'].get('b' if room['players'].get('w') == name else 'w')
                if not other:
                    await ws.send(json.dumps({'type': 'error', 'text': 'Соперник не в комнате'}, ensure_ascii=False))
                    continue
                if t == 'undo':
                    # откатываем пару полуходов: ход просящего и ответ соперника
                    mine_last = any(True for _ in g.history)
                    if not mine_last:
                        await ws.send(json.dumps({'type': 'error', 'text': 'Ходов ещё не было'}, ensure_ascii=False))
                        continue
                    if len(g.history) < 2:
                        await ws.send(json.dumps({'type': 'error', 'text': 'Соперник ещё не сделал ход — жди его хода'}, ensure_ascii=False))
                        continue
                    room['requests']['undo'] = name
                else:
                    room['requests']['draw'] = name
                kind_txt = 'отменить последний ход' if t == 'undo' else 'ничью'
                await broadcast(room, {
                    'type': 'request', 'kind': t, 'from': name,
                    'text': f'{name} предлагает {kind_txt}. Согласиться?'})

            elif t == 'answer' and room:
                kind = msg.get('kind')      # undo | draw
                ok = bool(msg.get('ok'))
                asker = room['requests'].get(kind)
                if not asker:
                    continue
                g = room['game']
                room['requests'].pop(kind, None)
                if kind == 'undo':
                    if ok:
                        g.undo(2)   # пара полуходов = один полный ход
                        room['premove'] = None
                        await broadcast(room, {'type': 'system', 'text': f'{asker} отменил последний ход', 'state': g.state()})
                        await broadcast(room, {'type': 'request_done', 'kind': 'undo', 'ok': True})
                    else:
                        await broadcast(room, {'type': 'request_done', 'kind': 'undo', 'ok': False,
                                               'text': f'{name} отклонил отмену хода'})
                elif kind == 'draw':
                    if ok:
                        g.resigned = None
                        g.draw_agreed = True
                        await broadcast(room, {'type': 'system', 'text': 'Игроки согласились на ничью', 'state': g.state()})
                        await broadcast(room, {'type': 'request_done', 'kind': 'draw', 'ok': True})
                        notify_lobby()
                    else:
                        await broadcast(room, {'type': 'request_done', 'kind': 'draw', 'ok': False,
                                               'text': f'{name} отклонил ничью'})

            elif t == 'resign' and room:
                g = room['game']
                if g.status() != 'active':
                    await ws.send(json.dumps({'type': 'error', 'text': 'Партия уже окончена'}, ensure_ascii=False))
                    continue
                if name not in (room['players'].get('w'), room['players'].get('b')):
                    continue
                loser = 'w' if room['players'].get('w') == name else 'b'
                g.resigned = loser
                await broadcast(room, {'type': 'system',
                                       'text': f'{name} сдался', 'state': g.state()})
                await broadcast(room, {'type': 'request_done', 'kind': 'resign', 'ok': True})
                notify_lobby()
    except Exception:
        pass
    finally:
        LOBBY.discard(ws)
        if room:
            leave_room(room, ws)

def localStorage_name(msg):
    return msg.get('playerName') or msg.get('name') or 'Гость'

def ws_is_dead(ws):
    """Соединение закрыто/зависло (совместимо со старым и новым websockets API)."""
    try:
        if hasattr(ws, 'state'):
            from websockets.protocol import State
            return ws.state is not State.OPEN
        return bool(ws.closed)
    except Exception:
        return True


async def clock_ticker():
    """Раз в секунду: просрочка хода -> поражение; чистка мёртвых соединений."""
    while True:
        await asyncio.sleep(1)
        # вычищаем закрытые ws из комнат (страховка от зависших «игроков»)
        for r in list(ROOMS.values()):
            for ws in list(r['clients'].keys()):
                if ws_is_dead(ws):
                    leave_room(r, ws)
            for ws in list(LOBBY):
                if ws_is_dead(ws):
                    LOBBY.discard(ws)
        for r in list(ROOMS.values()):
            g = r['game']
            if (g.move_time and g.deadline and g.status() == 'active'
                    and now() > g.deadline):
                g.flag_fell = g.turn_color()   # просрочил текущий
                g.deadline = None
                await broadcast(r, {'type': 'system',
                                    'text': 'Время на ход истекло',
                                    'state': g.state()})
                notify_lobby()

async def ws_server():
    async with websockets.serve(handler, '0.0.0.0', 8091, ping_interval=20):
        print('WS on 8091')
        asyncio.ensure_future(clock_ticker())
        await asyncio.Future()

class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        SimpleHTTPRequestHandler.end_headers(self)

def main():
    import threading, os
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    httpd = ThreadingHTTPServer(('0.0.0.0', 8090), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print('HTTP on 8090, serving', os.getcwd())
    asyncio.run(ws_server())

if __name__ == '__main__':
    main()
