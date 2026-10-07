#!/usr/bin/env python3
"""E2E: ходы наперёд (premove) — валидный применяется автоматически, невалидный отменяется; SAN-история."""
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
        await a.send(json.dumps({'type': 'create', 'name': 'Pre', 'playerName': 'A'}))
        w, _ = await recv_until(a, 'welcome')
        rid = w['room']['id']
        await b.send(json.dumps({'type': 'join', 'name': 'B', 'room': rid, 'roomName': 'Pre'}))
        await drain(a); await drain(b)

        # 1. premove в свою очередь — отказ
        await a.send(json.dumps({'type': 'premove', 'from': [4, 6], 'to': [4, 4]}))
        pm, _ = await recv_until(a, 'premove')
        assert pm and pm['ok'] is False, pm
        print('1. Premove в свою очередь отклонён:', pm.get('text'))

        # 2. Anna ходит e4; Bob (в свою очередь ДО хода) — стоп: ход Bob после e4.
        await a.send(json.dumps({'type': 'move', 'from': [4, 6], 'to': [4, 4]}))
        mv, _ = await recv_until(a, 'move')
        assert mv and mv['state']['san'] == [['e4', None]], mv['state']['san']
        print('2. SAN-история: e4 →', mv['state']['san'])
        await drain(b)

        # 3. Anna (не её очередь — ход чёрных) заготавливает ход f2f4
        await drain(a)
        await a.send(json.dumps({'type': 'premove', 'from': [5, 6], 'to': [5, 4]}))  # f2f4
        pm, _ = await recv_until(a, 'premove')
        assert pm and pm['ok'] is True, pm
        print('3. Premove заготовлен: f2→f4')
        await drain(b)

        # Bob ходит e5 → очередь Anna → premove f2f4 применяется АВТОМАТИЧЕСКИ
        await b.send(json.dumps({'type': 'move', 'from': [4, 1], 'to': [4, 3]}))  # e5
        mv, _ = await recv_until(b, 'move')
        assert mv and mv['from'] == [4, 1], mv
        # ждём АВТО-ход Anna
        mv2, _ = await recv_until(b, 'move', timeout=4, collect_errors=False)
        assert mv2 and mv2.get('premoved') is True and mv2['from'] == [5, 6], mv2
        board = mv2['state']['board']
        assert board[4][5] == 'wP', board
        print('4. Bob сходил e5 → premove Anna f2f4 применён автоматически')

        # 4. SAN: [[e4,e5],[f4,None]]
        sans = mv2['state']['san']
        assert sans == [['e4', 'e5'], ['f4', None]], sans
        print('5. SAN-история:', sans)

        # 5. невалидный premove: очередь Bob (после авто-f4 ход чёрных) — Bob
        #    заготавливать не может (его очередь). Поэтому: Bob ХОДИТ d7d5,
        #    очередь Anna; Bob заготавливает c7c6, Anna играет e4xd5 (занимает d5
        #    и... c6 не блокирует? c7c6 поле c6 — НЕ заблокировано exd5!
        #    ОТМЕНА: Bob premove d7d5, Anna exd5 — d5 занят → отмена!
        # СТОП: ход уже Bob. План: Bob ходит h7h6 (не мешает), Anna заготовит?—
        #    нельзя (её очередь). Anna ходит g2g3, очередь Bob: Bob premove d7d5,
        #    Anna ходит e4-e5? e5 СВОБОДНА? у Bob пешка e5 (ход 2: e5!) — e4e5
        #    = ВЗЯТИЕ e5! После этого Bob premove d7d5: d5 свободен?? exd5 нет.
        #    Отмена невозможна так просто...
        # РЕШЕНИЕ: Bob premove ФЕРЗЁМ d8h4 (диагональ e7-f6-g5-h4... e7 ЗАНЯТА
        #    пешкой e5? НЕТ: e7 опустела после e5!) — Bob premove Qd8h4, Anna
        #    ходит g2g3 — h4 теперь под g3?? g3 ПЕШКА бьёт h4! Легальность Qh4:
        #    поле h4 свободно — ХОД ЛЕГАЛЕН (ферзь d8-e7-f6-g5-h4). ОТМЕНА требует
        #    НЕлегальности: Anna ходит Nh3?? ЛАДЬА h1 на h4... просто:
        #    Bob premove Qd8h4; Anna играет g2g4: g5 занято? нет g-пешка на g4,
        #    диагональ d8-h4 чиста... Qh4 легален. Не отменит.
        # ФИНАЛЬНЫЙ ПЛАН: Bob premove d7d5 (легален: d5 пуст), Anna играет
        #    e4-e5?! СТОП e5 занята Bob. Anna exd5! — d5 ЗАХВАЧЕН → Bob d7d5
        #    становится нелегальным → ОТМЕНА. НО Bob уже сходил h6, ход Anna:
        #    Anna exd5, потом premove Bob был ДО? ПОРЯДОК: очередь Bob после g3.
        #    Значит: Anna g3 (ход 3), очередь Bob: Bob premove d7d5; Anna ходит
        #    e4-e5 НЕЛЬЗЯ (занято) — Anna exd5 нечем... ПЕШКА e4 бьёт d5! Anna
        #    exd5: пешка e4 на d5. Bob premove d7d5: d5 занят белыми → отмена!
        # (позиция: после авто-f4 ход чёрных)
        await b.send(json.dumps({'type': 'move', 'from': [7, 1], 'to': [7, 2]}))  # Bob h6
        mv, _ = await recv_until(b, 'move')
        assert mv and mv['from'] == [7, 1], mv
        await drain(a)
        # Anna g3 (создаёт позицию для exd5)
        await a.send(json.dumps({'type': 'move', 'from': [6, 6], 'to': [6, 5]}))  # g2g3
        mv, _ = await recv_until(a, 'move')
        assert mv and mv['from'] == [6, 6], mv
        await drain(b)
        # очередь Bob: ANNA заготавливает (не её очередь) ход e4-e5 —
        # после хода Bob он станет нелегален (e5 занята чёрной пешкой)
        await drain(a)
        await a.send(json.dumps({'type': 'premove', 'from': [4, 4], 'to': [4, 3]}))  # e4e5
        pm, _ = await recv_until(a, 'premove')
        assert pm and pm['ok'] is True, pm
        print('6. Anna заготовила e4-e5 (обречённый ход)')
        await drain(b)
        # Bob ходит d7d5 → try_premove: e4e5 нелегален (e5 занята) → отмена
        await b.send(json.dumps({'type': 'move', 'from': [3, 1], 'to': [3, 3]}))  # d7d5
        mv, _ = await recv_until(b, 'move')
        assert mv and mv['from'] == [3, 1], mv
        # Anna получает ОТМЕНУ
        pm2, _ = await recv_until(a, 'premove', timeout=4, collect_errors=False)
        assert pm2 and pm2['ok'] is False, pm2
        print('7. Bob сходил d5 → premove Anna e4-e5 отменён:', pm2.get('text'))
        board = mv['state']['board']
        assert board[3][3] == 'bP' and board[4][4] == 'wP', board  # d5=чёрная, e4=белая
        print('8. Доска корректна: авто-хода не было')

        # 6. clear premove
        await b.send(json.dumps({'type': 'premove', 'from': [3, 1], 'to': [3, 2], 'clear': True}))
        pm, _ = await recv_until(b, 'premove')
        assert pm and pm.get('cleared') is True, pm
        print('8. Premove снят игроком')

    print()
    print('ALL E2E TESTS PASSED')

asyncio.run(main())
