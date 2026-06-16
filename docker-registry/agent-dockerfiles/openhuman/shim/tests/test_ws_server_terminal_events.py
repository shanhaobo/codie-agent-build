"""Phase 4: terminal v1 events.

Bridge's v1 path resolves the dispatch future directly off
`{type:'event',event:'agent.complete',data:{content}}` (and fails it off
`agent.error` data.error) — faster and more robust than waiting for the next
agent.wait poll round-trip.
"""
from __future__ import annotations

from conftest import make_fake_events, shim_server


async def test_chat_done_emits_agent_complete_event(chat_rpc):
    q, bind = make_fake_events()
    async with shim_server(chat_rpc, bind(chat_rpc)) as client:
        await client.request('abc', 'chat.send', {'message': 'hi'})
        send_res = await client.frame_where(
            lambda f: f.get('type') == 'response' and f.get('ref_id') == 'abc')
        run_id = send_res['result']['run_id']

        q.put_nowait(('chat_done', {
            'request_id': 'req-1',
            'full_response': 'final answer',
        }))

        ev = await client.frame_where(
            lambda f: f.get('type') == 'event' and f.get('event') == 'agent.complete')
        assert ev['v'] == 1
        assert ev['data']['run_id'] == run_id
        assert ev['data']['content'] == 'final answer'


async def test_chat_error_emits_agent_error_event_and_wait_fails(chat_rpc):
    q, bind = make_fake_events()
    async with shim_server(chat_rpc, bind(chat_rpc)) as client:
        await client.request('abc', 'chat.send', {'message': 'hi'})
        send_res = await client.frame_where(
            lambda f: f.get('type') == 'response' and f.get('ref_id') == 'abc')
        run_id = send_res['result']['run_id']

        q.put_nowait(('chat_error', {
            'request_id': 'req-1',
            'message': 'provider exploded',
            'error_type': 'provider',
        }))

        ev = await client.frame_where(
            lambda f: f.get('type') == 'event' and f.get('event') == 'agent.error')
        assert ev['data']['run_id'] == run_id
        assert ev['data']['error'] == 'provider exploded'

        # A subsequent agent.wait reports the same failure, non-retryably.
        await client.request('w1', 'agent.wait', {'run_id': run_id, 'timeout': 5})
        wait_res = await client.frame_where(
            lambda f: f.get('type') == 'response' and f.get('ref_id') == 'w1')
        assert wait_res['error']['retryable'] is False
        assert 'provider exploded' in wait_res['error']['message']


async def test_events_for_other_requests_are_ignored(chat_rpc):
    q, bind = make_fake_events()
    async with shim_server(chat_rpc, bind(chat_rpc)) as client:
        await client.request('abc', 'chat.send', {'message': 'hi'})
        send_res = await client.frame_where(
            lambda f: f.get('type') == 'response' and f.get('ref_id') == 'abc')
        run_id = send_res['result']['run_id']

        # A different request's completion must not terminate this run.
        q.put_nowait(('chat_done', {
            'request_id': 'someone-else',
            'full_response': 'not yours',
        }))
        q.put_nowait(('chat_done', {
            'request_id': 'req-1',
            'full_response': 'yours',
        }))

        ev = await client.frame_where(
            lambda f: f.get('type') == 'event' and f.get('event') == 'agent.complete')
        assert ev['data']['run_id'] == run_id
        assert ev['data']['content'] == 'yours'
