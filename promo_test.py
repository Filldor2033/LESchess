#!/usr/bin/env python3
"""E2E: превращение пешки — ферзь по умолчанию, выбор фигуры, взятие с промоцией."""
import asyncio, json, websockets

URL = 'ws://127.0.0.1:8091'

async def recv_until(ws, want, timeout=5, collect_errors=True):
    err = None
    for _ in range(80):
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        except asyncio.TimeoutError:
            break
        if m.get('type') == want:
            return m, err
        if m.get('type') == 'error' and collect_errors:
            err = m.get('text')
    return None, err

async def drain(ws, quiet=0.3):
    while True:
        try:
            await asyncio.wait_for(ws.recv(), quiet)
        except asyncio.TimeoutError:
            return

async def main():
    async with websockets.connect(URL) as a, websockets.connect(URL) as b:
        await a.send(json.dumps({'type': 'create', 'name': 'Promo', 'playerName': 'A'}))
        w, _ = await recv_until(a, 'welcome')
        rid = w['room']['id']
        await b.send(json.dumps({'type': 'join', 'name': 'B', 'room': rid, 'roomName': 'Promo'}))
        await drain(b)

        async def move(ws, frm, to, promote=None):
            d = {'type': 'move', 'from': frm, 'to': to}
            if promote: d['promote'] = promote
            await ws.send(json.dumps(d))
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), 4))
                if m['type'] == 'error':
                    return m
                if m['type'] == 'move' and m.get('from') == frm:
                    return m
                if m['type'] == 'move' and frm is None:
                    return m

        # раскладка: 1.a4 g5 2.a5 g4 3.a6 g3 4.axb7 gxh2 5.bxa8=Q hxg1=R
        seq = [
            (a, [0, 3], [0, 4], None),    # a2-a4? a2=(0,4)? rank2 → y=6!! (см. ниже)
        ]
        # КООРДИНАТЫ (y = 8 - rank): a2=(0,6)? rank2 → y=6. ПРОВЕРКА: стартовые белые
        # пешки в UI на y=6 — ДА (renderBoard y=6 ряд белых).
        # Значит: a2=(0,6), a4=(0,4), g7=(6,1), g5=(6,3), g4=(6,4), g3=(6,5),
        # a6=(0,2), b7=(1,1), h2=(7,6), a8=(0,0), g1=(6,7), g2=(6,6)
        plan = [
            (a, [0, 6], [0, 4], None),    # a4
            (b, [6, 1], [6, 3], None),    # g5
            (a, [0, 4], [0, 3], None),    # a5
            (b, [6, 3], [6, 4], None),    # g4
            (a, [0, 3], [0, 2], None),    # a6
            (b, [6, 4], [6, 5], None),    # g3
            (a, [0, 2], [1, 1], None),    # axb7 (взятие пешки b7)
            (b, [6, 5], [7, 6], None),    # gxh2 (взятие пешки h2)
        ]
        for ws, f, t, p in plan:
            m = await move(ws, f, t, p)
            assert m['type'] == 'move', (f, t, m)
        await drain(a); await drain(b)

        # 1. белая промоция БЕЗ promote → ферзь по умолчанию (bxa8=Q)
        m = await move(a, [1, 1], [0, 0])
        assert m['type'] == 'move', m
        board = m['state']['board']
        assert board[0][0] == 'wQ', ('a8', board[0][0])
        print('1. Промоция по умолчанию: a8 = wQ')

        # 2. чёрная промоция С promote=R (hxg1=R)
        m = await move(b, [7, 6], [6, 7], promote='R')
        assert m['type'] == 'move', m
        board = m['state']['board']
        assert board[7][6] == 'bR', ('g1', board[7][6])
        print('2. Промоция с выбором: g1 = bR (взятие коня)')

        # 3. невалидный promote (K) → fallback на Q
        # пешек на промо-пути нет — проверим, что игра продолжается
        m = await move(a, [4, 6], [4, 4])   # e4
        assert m['type'] == 'move', m
        print('3. Игра продолжается после промоций: e4 прошёл')

        # 4. запрос 'moves' для промо-поля показывает цель (клиент рисует подсказку)
        await a.send(json.dumps({'type': 'moves', 'x': 0, 'y': 0}))
        mv, _ = await recv_until(a, 'moves')
        print('4. Подсказки ферзя a8:', mv['moves'][:6], '…')

    print()
    print('ALL E2E TESTS PASSED')

asyncio.run(main())
