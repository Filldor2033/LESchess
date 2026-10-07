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
    def __init__(self):
        self.b = chess.Board()
        self.moves = 0
        self.history = []    # стек сделанных (sx, sy, tx, ty) для отмены

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

    def apply_move(self, sx, sy, tx, ty):
        mv = chess.Move(chess.square(sx, 7 - sy), chess.square(tx, 7 - ty))
        if mv not in self.b.legal_moves: return None
        captured = self.piece(tx, ty)
        self.b.push(mv)
        self.moves += 1
        self.history.append((sx, sy, tx, ty))
        return {'captured': captured}

    def undo(self, halfmoves=2):
        """Откатить n полуходов (по умолчанию пара = один полный ход)."""
        if halfmoves > len(self.history):
            halfmoves = len(self.history)
        if halfmoves <= 0: return False
        for _ in range(halfmoves):
            self.b.pop()
            self.history.pop()
            self.moves = max(0, self.moves - 1)
        return True

    def status(self):
        if self.b.is_checkmate(): return 'checkmate'
        if self.b.is_stalemate(): return 'stalemate'
        if getattr(self, 'resigned', None): return 'resign'
        if getattr(self, 'draw_agreed', False): return 'draw'
        if self.b.is_insufficient_material(): return 'draw'
        if self.b.is_fifty_moves(): return 'draw'
        if self.b.is_repetition(3): return 'draw'
        return 'active'

    def result(self):
        if self.b.is_checkmate():
            return 'w' if self.b.turn == chess.BLACK else 'b'
        if getattr(self, 'resigned', None):
            return 'b' if self.resigned == 'w' else 'w'   # победил не сдавшийся
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

    def state(self):
        st = self.status()
        return {'board': self.board_grid(), 'turn': self.turn_color(),
                'status': st,
                'result': self.result() if st in ('checkmate', 'stalemate', 'draw', 'resign') else None,
                'moves': self.moves,
                'inCheck': self.b.is_check() if st == 'active' else False,
                'material': self.material()}


# ---------------- Комнаты и WS ----------------

ROOMS = {}      # room_id -> {'id','name','password','created','game','clients':{ws:name},'chat':[],'players':{...}}
LOBBY = set()   # соединения, смотрящие список комнат (ещё не в комнате)
CONNS = set()

def now(): return int(time.time())

def room_public(r):
    return {'id': r['id'], 'name': r['name'], 'players': len(r['clients']),
            'status': r['game'].status(), 'created': r['created'],
            'locked': bool(r.get('password'))}

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

async def enter_room(ws, r, name):
    """Поместить соединение в комнату: дедуп имени, цвет, welcome, нотификации."""
    n = 1
    base = name
    while name in r['clients'].values():
        n += 1; name = f'{base} {n}'
    r['clients'][ws] = name
    if r['players']['w'] is None: r['players']['w'] = name
    elif r['players']['b'] is None: r['players']['b'] = name
    LOBBY.discard(ws)
    await ws.send(json.dumps({'type': 'welcome', 'name': name, 'color': 'w' if r['players']['w']==name else 'b',
                              'room': room_public(r), 'state': r['game'].state(),
                              'players': r['players'], 'chat': r['chat'][-60:]}, ensure_ascii=False))
    await broadcast(r, {'type': 'system', 'text': f'{name} присоединился'}, exclude=ws)
    broadcast_sync(r, {'type': 'room', 'room': room_public(r)})
    broadcast_sync(r, {'type': 'players', 'players': r['players']})
    notify_lobby()
    return name

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
                if len(r['clients']) >= 2:
                    await ws.send(json.dumps({'type': 'error', 'text': 'Комната заполнена (макс. 2 игрока)'}, ensure_ascii=False))
                    room = None
                    continue
                name = await enter_room(ws, r, name)
            elif t == 'create' and not room:
                # создать комнату со своим названием и паролем
                rname = (msg.get('name') or '').strip()[:40]
                password = (msg.get('password') or '').strip()[:40]
                if not rname:
                    await ws.send(json.dumps({'type': 'error', 'text': 'Придумай название комнаты'}, ensure_ascii=False))
                    continue
                name = (msg.get('playerName') or localStorage_name(msg) or 'Гость')
                name = (name or 'Гость').strip()[:20] or 'Гость'
                room_id = 'r-' + secrets.token_hex(4)
                r = {'id': room_id, 'name': rname, 'password': password, 'created': now(),
                     'game': Game(), 'clients': {}, 'chat': [], 'players': {'w': None, 'b': None},
                     'requests': {}}
                ROOMS[room_id] = r
                room = r
                name = await enter_room(ws, r, name)
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
                res = g.apply_move(sx, sy, tx, ty)
                await broadcast(room, {'type': 'move', 'from': [sx, sy], 'to': [tx, ty],
                                       'piece': res['captured'] and None, 'state': g.state()})
                if g.status() != 'active':
                    notify_lobby()   # комната «в игре» -> «свободна/окончена» в лобби
            elif t == 'reset' and room:
                if not room['players'].get('w') or not room['players'].get('b'):
                    await ws.send(json.dumps({'type': 'error', 'text': 'Ждём второго игрока'}, ensure_ascii=False))
                    continue
                room['game'] = Game()
                room['requests'].clear()
                await broadcast(room, {'type': 'system', 'text': 'Новая партия', 'state': room['game'].state()})
                broadcast_sync(room, {'type': 'players', 'players': room['players']})
                notify_lobby()
            elif t == 'moves' and room:
                mv = room['game'].legal_moves(msg['x'], msg['y'])
                await ws.send(json.dumps({'type': 'moves', 'x': msg['x'], 'y': msg['y'], 'moves': [list(m) for m in mv]}))

            # ---------- Запросы между игроками: отмена хода / ничья / сдача ----------
            elif t in ('undo', 'draw') and room:
                g = room['game']
                if g.status() != 'active':
                    await ws.send(json.dumps({'type': 'error', 'text': 'Партия уже окончена'}, ensure_ascii=False))
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

async def ws_server():
    async with websockets.serve(handler, '0.0.0.0', 8091, ping_interval=20):
        print('WS on 8091')
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
