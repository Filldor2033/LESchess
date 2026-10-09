#!/usr/bin/env python3
"""Тест: ход юзера + премув + ход бота — последовательность moveNo без пропусков,
бот и человек чередуются (анти-двойной-ход бота)."""
import asyncio, json, websockets, time

URL = 'ws://127.0.0.1:8091'

async def recv(ws, timeout=3):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))

async def main():
    a = await websockets.connect(URL)
    await a.send(json.dumps({'type': 'create', 'name': 'DblBot5', 'playerName': 'A'}))
    rid = None
    while rid is None:
        m = await recv(a)
        if m['type'] == 'welcome':
            rid = m['room']['id']
    # позвать бота-мастера (думает дольше — параллельные bot_turn вероятнее)
    await a.send(json.dumps({'type': 'bot', 'level': 'master'}))
    for _ in range(20):
        m = await recv(a, 4)
        if m['type'] == 'players' and 'Компьютер' in str(m['players']):
            break

    moves = []
    t0 = time.time()
    async def drain():
        while True:
            try:
                m = await recv(a, 0.4)
            except asyncio.TimeoutError:
                continue
            if m['type'] == 'move':
                moves.append((bool(m.get('bot')), m['from'], m['to'], m['state']['moves']))
    drainer = asyncio.ensure_future(drain())
    try:
        await asyncio.sleep(0.1)
        # юзер: e4 + мгновенный премув a3 — применится после хода бота
        await a.send(json.dumps({'type': 'move', 'from': [4, 6], 'to': [4, 4]}))
        await a.send(json.dumps({'type': 'premove', 'from': [0, 6], 'to': [0, 5]}))
        await asyncio.sleep(7)
    except asyncio.TimeoutError:
        pass
    finally:
        drainer.cancel()

    print('ходы (bot?, from, to, moveNo):')
    for mv in moves:
        print('  ', mv)
    nums = [m[3] for m in moves]
    seq_ok = nums == sorted(nums) and len(nums) == len(set(nums))
    flags = [('B' if m[0] else 'H') for m in moves]
    # юзер белые: чередование H,B,H,B...
    alt_ok = all(f == ('H' if i % 2 == 0 else 'B') for i, f in enumerate(flags))
    print('moveNo строго возрастают без повторов:', seq_ok, nums)
    print('чередование человек/бот:', alt_ok, flags)
    assert seq_ok and alt_ok, 'ПОРЯДОК ХОДОВ НАРУШЕН'
    await a.close()
    print('OK')

asyncio.run(main())
