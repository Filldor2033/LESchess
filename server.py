#!/usr/bin/env python3
"""LESchess — локальные веб-шахматы с чатом и комнатами. Один файл, без зависимостей."""
import asyncio, json, time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import websockets

def new_board():
    return [
        ['bR','bN','bB','bQ','bK','bB','bN','bR'],
        ['bP']*8, [None]*8, [None]*8, [None]*8, [None]*8,
        ['wP']*8,
        ['wR','wN','wB','wQ','wK','wB','wN','wR'],
    ]

PIECE_VAL = {'K':0,'Q':9,'R':5,'B':3,'N':3,'P':1}

class Game:
    def __init__(self):
        self.board = new_board()
        self.turn = 'w'
        self.history = []
        self.moves = 0
        self.status = 'active'
        self.result = None

    def clone(self):
        g = Game()
        g.board = [r[:] for r in self.board]
        g.turn = self.turn
        g.moves = self.moves
        return g

    def piece(self, x, y):
        return self.board[y][x]

    def find_king(self, color):
        for y in range(8):
            for x in range(8):
                p = self.board[y][x]
                if p == color + 'K':
                    return (x, y)
        return None

    def pseudo_moves(self, x, y, for_attack=False):
        p = self.piece(x, y)
        if not p: return []
        color, kind = p[0], p[1]
        moves = []
        D = [(1,0),(-1,0),(0,1),(0,-1)]
        DD = [(1,1),(1,-1),(-1,1),(-1,-1)]
        N = [(1,2),(2,1),(2,-1),(1,-2),(-1,-2),(-2,-1),(-2,1),(-1,2)]
        def inside(x, y): return 0 <= x < 8 and 0 <= y < 8
        def add(tx, ty):
            t = self.piece(tx, ty)
            if t is None:
                moves.append((tx, ty))
                return True
            if t[0] != color:
                moves.append((tx, ty))
            return False
        if kind == 'P':
            dirn = -1 if color == 'w' else 1
            start = 6 if color == 'w' else 1
            if for_attack:
                for dx in (-1, 1):
                    tx, ty = x+dx, y+dirn
                    if inside(tx, ty): moves.append((tx, ty))
            else:
                if inside(x, y+dirn) and self.piece(x, y+dirn) is None:
                    moves.append((x, y+dirn))
                    if y == start and self.piece(x, y+2*dirn) is None:
                        moves.append((x, y+2*dirn))
                for dx in (-1, 1):
                    tx, ty = x+dx, y+dirn
                    if inside(tx, ty):
                        t = self.piece(tx, ty)
                        if t is not None and t[0] != color:
                            moves.append((tx, ty))
        elif kind == 'N':
            for dx, dy in N:
                tx, ty = x+dx, y+dy
                if inside(tx, ty): add(tx, ty)
        elif kind == 'K':
            for dx, dy in D + DD:
                tx, ty = x+dx, y+dy
                if inside(tx, ty): add(tx, ty)
        else:
            dirs = D if kind == 'R' else DD if kind == 'B' else D + DD
            for dx, dy in dirs:
                tx, ty = x+dx, y+dy
                while inside(tx, ty):
                    if not add(tx, ty): break
                    tx, ty = tx+dx, ty+dy
        return moves

    def in_check(self, color):
        k = self.find_king(color)
        if not k: return True
        kx, ky = k
        opp = 'b' if color == 'w' else 'w'
        for y in range(8):
            for x in range(8):
                if self.piece(x, y) and self.piece(x, y)[0] == opp:
                    if (kx, ky) in self.pseudo_moves(x, y, for_attack=True):
                        return True
        return False

    def legal_moves(self, x, y):
        p = self.piece(x, y)
        if not p or self.status != 'active': return []
        if p[0] != self.turn: return []
        res = []
        for tx, ty in self.pseudo_moves(x, y):
            g2 = self.clone()
            g2.board[ty][tx] = g2.board[y][x]
            g2.board[y][x] = None
            # пешка дошла до края — повышаем для корректности проверки шаха (фигурка не важна)
            if not g2.in_check(p[0]):
                res.append((tx, ty))
        return res

    def has_any_move(self, color):
        for y in range(8):
            for x in range(8):
                p = self.piece(x, y)
                if p and p[0] == color:
                    if self.legal_moves(x, y):
                        return True
        return False

    def apply_move(self, sx, sy, tx, ty):
        p = self.piece(sx, sy)
        captured = self.piece(tx, ty)
        self.board[ty][tx] = p
        self.board[sy][sx] = None
        # превращение пешки
        if p[1] == 'P' and ty in (0, 7):
            self.board[ty][tx] = p[0] + 'Q'
        self.turn = 'w' if self.turn == 'b' else 'b'
        self.moves += 1
        self.history.append({'from': [sx, sy], 'to': [tx, ty], 'piece': p, 'captured': captured})
        # проверка конца игры
        opp = self.turn
        if not self.has_any_move(opp):
            if self.in_check(opp):
                self.status = 'checkmate'
                self.result = 'w' if opp == 'b' else 'b'
            else:
                self.status = 'stalemate'
                self.result = 'draw'
        return {'captured': captured}

    def material(self):
        d = 0
        for row in self.board:
            for p in row:
                if p:
                    v = PIECE_VAL[p[1]]
                    d += v if p[0] == 'w' else -v
        return d

    def state(self):
        return {'board': self.board, 'turn': self.turn, 'status': self.status,
                'result': self.result, 'moves': self.moves,
                'inCheck': self.in_check(self.turn) if self.status == 'active' else False,
                'material': self.material()}


# ---------------- Комнаты и WS ----------------

ROOMS = {}      # room_id -> {'id','name','created','game','clients':{ws:name},'chat':[],'players':{...}}
CONNS = set()

def now(): return int(time.time())

def room_public(r):
    return {'id': r['id'], 'name': r['name'], 'players': len(r['clients']),
            'status': r['game'].status, 'created': r['created']}

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
    if not r['clients'] and len(ROOMS) > 1:
        ROOMS.pop(r['id'], None)

def broadcast_sync(r, obj):
    asyncio.ensure_future(broadcast(r, obj))

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
                name = (msg.get('name') or 'Гость').strip()[:20] or 'Гость'
                room_id = msg.get('room')
                r = ROOMS.get(room_id)
                if not r:
                    # создать если не существует
                    r = {'id': room_id, 'name': msg.get('roomName') or room_id, 'created': now(),
                         'game': Game(), 'clients': {}, 'chat': [], 'players': {'w': None, 'b': None}}
                    ROOMS[room_id] = r
                room = r
                if len(r['clients']) >= 2:
                    await ws.send(json.dumps({'type': 'error', 'text': 'Комната заполнена (макс. 2 игрока)'}))
                    continue
                n = 1
                base = name
                while name in r['clients'].values():
                    n += 1; name = f'{base} {n}'
                r['clients'][ws] = name
                # назначение цвета
                if r['players']['w'] is None: r['players']['w'] = name
                elif r['players']['b'] is None: r['players']['b'] = name
                await ws.send(json.dumps({'type': 'welcome', 'name': name, 'color': 'w' if r['players']['w']==name else 'b',
                                          'room': room_public(r), 'state': r['game'].state(),
                                          'players': r['players'], 'chat': r['chat'][-60:]}, ensure_ascii=False))
                await broadcast(r, {'type': 'system', 'text': f'{name} присоединился'}, exclude=ws)
                broadcast_sync(r, {'type': 'room', 'room': room_public(r)})
                broadcast_sync(r, {'type': 'players', 'players': r['players']})
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
                if g.status != 'active' or g.turn != mycolor:
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
            elif t == 'reset' and room:
                if not room['players'].get('w') or not room['players'].get('b'):
                    await ws.send(json.dumps({'type': 'error', 'text': 'Ждём второго игрока'}, ensure_ascii=False))
                    continue
                room['game'] = Game()
                await broadcast(room, {'type': 'system', 'text': 'Новая партия', 'state': room['game'].state()})
                broadcast_sync(room, {'type': 'players', 'players': room['players']})
            elif t == 'moves' and room:
                mv = room['game'].legal_moves(msg['x'], msg['y'])
                await ws.send(json.dumps({'type': 'moves', 'x': msg['x'], 'y': msg['y'], 'moves': [list(m) for m in mv]}))
    except Exception:
        pass
    finally:
        if room:
            leave_room(room, ws)

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
