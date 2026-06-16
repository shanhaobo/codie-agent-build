"""Phase 3: token streaming — core SSE `text_delta` → v1 `agent.delta`.

Bridge's `gatewayV1DeltaText` (agent_dispatcher.dart) consumes
{type:'event',event:'agent.delta',data:{text}} frames carrying INCREMENTAL
fragments. Core's `text_delta` WebChannelEvent already carries an incremental
`delta` (AgentProgress::TextDelta), so this is a 1:1 translation — no
cumulative diffing needed (unlike openClaw's chat.delta).
"""
from __future__ import annotations

from conftest import make_fake_events, shim_server


async def test_text_deltas_stream_as_agent_delta_frames_in_order(chat_rpc):
    q, bind = make_fake_events()
    async with shim_server(chat_rpc, bind(chat_rpc)) as client:
        await client.request('abc', 'chat.send', {'message': 'hi'})
        await client.frame_where(
            lambda f: f.get('type') == 'response' and f.get('ref_id') == 'abc')

        for frag in ('Hel', 'lo ', 'world'):
            q.put_nowait(('text_delta', {
                'request_id': 'req-1',
                'delta': frag,
                'delta_kind': 'text',
                'round': 1,
            }))
        q.put_nowait(('chat_done', {
            'request_id': 'req-1',
            'full_response': 'Hello world',
        }))

        got: list[str] = []
        while True:
            frame = await client.next_frame()
            if frame.get('event') == 'agent.delta':
                got.append(frame['data']['text'])
            elif frame.get('event') == 'agent.complete':
                break
        assert got == ['Hel', 'lo ', 'world']


async def test_non_text_delta_events_are_not_forwarded(chat_rpc):
    q, bind = make_fake_events()
    async with shim_server(chat_rpc, bind(chat_rpc)) as client:
        await client.request('abc', 'chat.send', {'message': 'hi'})
        await client.frame_where(
            lambda f: f.get('type') == 'response' and f.get('ref_id') == 'abc')

        # thinking / tool-args deltas and empty text must stay shim-internal.
        q.put_nowait(('thinking_delta', {
            'request_id': 'req-1', 'delta': 'pondering...', 'delta_kind': 'thinking'}))
        q.put_nowait(('tool_args_delta', {
            'request_id': 'req-1', 'delta': '{"url":', 'delta_kind': 'tool_args'}))
        q.put_nowait(('text_delta', {
            'request_id': 'req-1', 'delta': '', 'delta_kind': 'text'}))
        q.put_nowait(('tool_call', {
            'request_id': 'req-1', 'tool_name': 'web_search'}))
        q.put_nowait(('chat_done', {
            'request_id': 'req-1', 'full_response': 'done'}))

        deltas = []
        while True:
            frame = await client.next_frame()
            if frame.get('event') == 'agent.delta':
                deltas.append(frame['data']['text'])
            elif frame.get('event') == 'agent.complete':
                break
        assert deltas == []
