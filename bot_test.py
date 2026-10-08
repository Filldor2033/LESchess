#!/usr/bin/env python3
"""E2E: игра с компьютером — приглашение, ходы бота, undo/draw, изгнание, лобби-флаг."""
import asyncio, json, websockets

URL = 'ws://127.0.0.1:8091'

async def recv_until(ws, want, timeout=6, collect_errors=True):
    err = None
    for _ in range(100):
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
    async with websockets.connect(URL) as a:
        await a.send(json.dumps({'type': 'create', 'name': 'BotE2E', 'playerName': 'A'}))
        w, _ = await recv_until(a, 'welcome')
        rid = w['room']['id']
        assert w['room']['bot'] is False
        print('1. Комната без бота (bot=False)')

        # позвать бота
        await a.send(json.dumps({'type': 'bot'}))
        sysm, _ = await recv_until(a, 'system')
        assert sysm and 'Компьютер' in sysm['text'], sysm
        pl, _ = await recv_until(a, 'players')
        assert pl and 'Компьютер' in (pl['players'].get('b') or ''), pl
        print('2. Компьютер позван: ', pl['players'])

        # ход человека → бот отвечает автоматически
        await a.send(json.dumps({'type': 'move', 'from': [4, 6], 'to': [4, 4]}))  # e4
        botmove = None
        for _ in range(15):
            m, _ = await recv_until(a, 'move', timeout=4, collect_errors=False)
            if m and m.get('bot'):
                botmove = m
                break
        assert botmove, 'бот не ответил'
        print('3. A: e4 → бот:', botmove['from'], '→', botmove['to'], '| san:', botmove['state']['san'])

        # ещё пара ходов
        await a.send(json.dumps({'type': 'move', 'from': [6, 6], 'to': [6, 4]}))
        botmove2 = None
        for _ in range(15):
            m, _ = await recv_until(a, 'move', timeout=4, collect_errors=False)
            if m and m.get('bot'):
                botmove2 = m
                break
        assert botmove2, 'бот не ответил второй раз'
        print('4. Бот сходил снова:', botmove2['from'], '→', botmove2['to'])

        # лобби-флаг
        async with websockets.connect(URL) as watcher:
            await watcher.send(json.dumps({'type': 'list'}))
            rooms, _ = await recv_until(watcher, 'rooms')
            br = [r for r in rooms['rooms'] if r['id'] == rid]
            assert br and br[0]['bot'] is True, br
            print('5. Лобби видит плашку bot=True, players =', br[0]['players'])

        # undo с ботом — соглашается
        await drain(a)
        await a.send(json.dumps({'type': 'undo'}))
        sysm, _ = await recv_until(a, 'system', timeout=4)
        assert sysm and 'отменить' in sysm['text'], sysm
        print('6. Undo: ', sysm['text'])

        # draw с ботом: материал равен → согласие
        await a.send(json.dumps({'type': 'draw'}))
        sysm, _ = await recv_until(a, 'system', timeout=4)
        assert sysm and 'ничью' in sysm['text'], sysm
        assert sysm['state']['status'] == 'draw'
        print('7. Draw: ', sysm['text'])

        # новая партия с ботом (стороны меняются: человек чёрные)
        await a.send(json.dumps({'type': 'reset'}))
        sysm, _ = await recv_until(a, 'system', timeout=4)
        assert sysm and 'поменялись' in sysm['text'], sysm
        pl, _ = await recv_until(a, 'players')
        print('8. Reset: ', pl['players'])
        # бот теперь белые — должен сходить первым
        botfirst = None
        for _ in range(15):
            m, _ = await recv_until(a, 'move', timeout=4, collect_errors=False)
            if m and m.get('bot'):
                botfirst = m
                break
        assert botfirst, 'бот-белые не сходил после reset'
        print('9. Бот (белые) начал новую партию:', botfirst['from'], '→', botfirst['to'])

        # выгнать бота
        await a.send(json.dumps({'type': 'bot', 'kick': True}))
        sysm, _ = await recv_until(a, 'system', timeout=4)
        assert sysm and ('покинул' in sysm['text'] or 'прервана' in sysm['text']), sysm
        pl, _ = await recv_until(a, 'players')
        assert 'Компьютер' not in str(pl['players']), pl
        print('10. Бот выгнан:', sysm['text'], '| players:', pl['players'])

        # повторно позвать можно
        await a.send(json.dumps({'type': 'bot'}))
        sysm, _ = await recv_until(a, 'system', timeout=4)
        assert sysm and 'присоединился' in sysm['text'], sysm
        print('11. Бота можно позвать снова')

        # дважды позвать нельзя
        await drain(a)
        await a.send(json.dumps({'type': 'bot'}))
        _, err = await recv_until(a, 'x', timeout=3)
        assert err == 'Компьютер уже в комнате', err
        print('12. Повторный вызов отклонён:', err)

    print()
    print('ALL E2E TESTS PASSED')

asyncio.run(main())
