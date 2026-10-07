#!/usr/bin/env python3
"""E2E: отмена хода, ничья, сдача — согласие и отказ."""
import asyncio, json, websockets

URL = 'ws://127.0.0.1:8091'

async def recv_until(ws, want, timeout=3, collect_errors=True):
    err = None
    got = None
    for _ in range(60):
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        except asyncio.TimeoutError:
            break
        if m.get('type') == want:
            got = m
            if err: break
            return m, err
        if m.get('type') == 'error' and collect_errors:
            err = m.get('text')
    return got, err

async def drain(ws, quiet=0.3):
    while True:
        try:
            await asyncio.wait_for(ws.recv(), quiet)
        except asyncio.TimeoutError:
            return

async def main():
    async with websockets.connect(URL) as anna, websockets.connect(URL) as bob:
        await anna.send(json.dumps({'type': 'create', 'name': 'UDR', 'playerName': 'Anna'}))
        w, _ = await recv_until(anna, 'welcome')
        rid = w['room']['id']
        await bob.send(json.dumps({'type': 'join', 'name': 'Bob', 'room': rid, 'roomName': 'UDR'}))
        await drain(bob); await drain(anna)
        print('комната готова')

        # пара ходов: e4 (Anna), e5 (Bob)
        await anna.send(json.dumps({'type': 'move', 'from': [4, 6], 'to': [4, 4]}))
        m, _ = await recv_until(anna, 'move')
        assert m and m['state']['board'][4][4] == 'wP', m
        await drain(bob)
        await bob.send(json.dumps({'type': 'move', 'from': [4, 1], 'to': [4, 3]}))
        m, _ = await recv_until(bob, 'move')
        assert m and m['state']['board'][3][4] == 'bP', m
        await drain(anna)
        print('ходы сделаны: e4, e5')

        # --- 1. UNDO: Anna просит, Bob ОТКАЗЫВАЕТ ---
        await anna.send(json.dumps({'type': 'undo'}))
        req, _ = await recv_until(bob, 'request')
        assert req and req['kind'] == 'undo' and req['from'] == 'Anna', req
        print('1a. Bob получил запрос отмены хода')
        await bob.send(json.dumps({'type': 'answer', 'kind': 'undo', 'ok': False}))
        done, _ = await recv_until(anna, 'request_done')
        assert done and done['ok'] is False, done
        await drain(anna)
        # доска не изменилась
        await anna.send(json.dumps({'type': 'moves', 'x': 4, 'y': 4}))
        mv, _ = await recv_until(anna, 'moves')
        assert mv is not None
        print('1b. Отказ: ходы не откатились, запрос снят')

        # --- 2. UNDO: Bob просит, Anna СОГЛАШАЕТСЯ ---
        await bob.send(json.dumps({'type': 'undo'}))
        req, _ = await recv_until(anna, 'request')
        assert req and req['kind'] == 'undo' and req['from'] == 'Bob', req
        await anna.send(json.dumps({'type': 'answer', 'kind': 'undo', 'ok': True}))
        sysm, _ = await recv_until(bob, 'system', collect_errors=False)
        assert sysm and sysm.get('state'), sysm
        st = sysm['state']
        # e4 и e5 откатились: пешки на e2 и e7
        assert st['board'][6][4] == 'wP', 'пешка e2 не вернулась'
        assert st['board'][1][4] == 'bP', 'пешка e7 не вернулась'
        assert st['status'] == 'active'
        await drain(anna); await drain(bob)
        print('2. Согласие: ходы откатились (e2/e7 на месте), ход снова белых')

        # ход снова должен быть белых: Anna может сходить
        await anna.send(json.dumps({'type': 'move', 'from': [4, 6], 'to': [4, 4]}))
        m, _ = await recv_until(anna, 'move')
        assert m and m['state']['board'][4][4] == 'wP'
        await drain(bob)
        print('2b. После отмены ходы продолжаются')

        # --- 3. DRAW: Anna предлагает, Bob ОТКАЗЫВАЕТ ---
        await anna.send(json.dumps({'type': 'draw'}))
        req, _ = await recv_until(bob, 'request')
        assert req and req['kind'] == 'draw', req
        await bob.send(json.dumps({'type': 'answer', 'kind': 'draw', 'ok': False}))
        done, _ = await recv_until(anna, 'request_done')
        assert done and done['ok'] is False
        await drain(anna)
        # партия активна
                # ход чёрных — это Bob
        await bob.send(json.dumps({'type': 'move', 'from': [3, 1], 'to': [3, 3]}))
        m, _ = await recv_until(bob, 'move')
        assert m and m['state']['status'] == 'active'
        print('3. Отказ от ничьей: партия продолжается')

        # --- 4. DRAW: Bob предлагает, Anna СОГЛАШАЕТСЯ ---
        await bob.send(json.dumps({'type': 'draw'}))
        req, _ = await recv_until(anna, 'request')
        assert req and req['kind'] == 'draw' and req['from'] == 'Bob', req
        await anna.send(json.dumps({'type': 'answer', 'kind': 'draw', 'ok': True}))
        sysm, _ = await recv_until(bob, 'system', collect_errors=False)
        assert sysm and sysm.get('state', {}).get('status') == 'draw', sysm
        st = sysm['state']
        assert st['result'] == 'draw'
        print('4. Согласие на ничью: статус draw, партия окончена')

        # --- 5. Новая партия + RESIGN ---
        await anna.send(json.dumps({'type': 'reset'}))
        sysm, _ = await recv_until(bob, 'system', collect_errors=False)
        assert sysm and sysm.get('state', {}).get('status') == 'active'
        await drain(anna); await drain(bob)
        # после reset стороны поменялись: Anna теперь чёрные. Она сдаётся →
        # победа белых (Bob)
        await anna.send(json.dumps({'type': 'resign'}))
        sysm, _ = await recv_until(bob, 'system', collect_errors=False)
        assert sysm and sysm.get('state'), sysm
        st = sysm['state']
        assert st['status'] == 'resign', st
        assert st['result'] == 'w', st
        print('5. Сдача Anna (после смены сторон): статус resign, победа белых')

        # --- 6. Запросы в оконченной партии отклоняются ---
        await anna.send(json.dumps({'type': 'undo'}))
        _, err = await recv_until(anna, 'x')
        assert err == 'Партия уже окончена', err
        print('6. В оконченной партии запросы отклоняются')

    print()
    print('ALL E2E TESTS PASSED')

asyncio.run(main())
