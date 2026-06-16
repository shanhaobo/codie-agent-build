"""Real SSE consumer (`shim.event_stream.subscribe_events`) against an
in-process ASGI app speaking axum-style SSE (`event:` + `data:` lines,
`:` keep-alive comments). No real sockets."""
from __future__ import annotations

import httpx

from shim.event_stream import subscribe_events

SSE_BODY = (
    b'event: text_delta\n'
    b'data: {"request_id":"r1","delta":"Hi","delta_kind":"text"}\n\n'
    b': keep-alive\n\n'
    b'data: not-json\n\n'
    b'event: chat_done\n'
    b'data: {"request_id":"r1","full_response":"Hi there"}\n\n'
)


def make_app(seen: dict):
    async def app(scope, receive, send):
        assert scope['type'] == 'http'
        seen['path'] = scope['path']
        seen['query'] = scope['query_string'].decode()
        await send({
            'type': 'http.response.start',
            'status': 200,
            'headers': [(b'content-type', b'text/event-stream')],
        })
        await send({'type': 'http.response.body', 'body': SSE_BODY, 'more_body': False})

    return app


async def test_subscribe_events_yields_named_events_and_skips_garbage():
    seen: dict = {}
    transport = httpx.ASGITransport(app=make_app(seen))
    events = []
    async with subscribe_events(
        'codie-run1', base_url='http://core', transport=transport,
    ) as stream:
        async for name, data in stream:
            events.append((name, data))

    assert seen['path'] == '/events'
    assert 'client_id=codie-run1' in seen['query']
    assert events == [
        ('text_delta', {'request_id': 'r1', 'delta': 'Hi', 'delta_kind': 'text'}),
        ('chat_done', {'request_id': 'r1', 'full_response': 'Hi there'}),
    ]
