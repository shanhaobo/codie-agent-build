"""Phase 1+2: v1 response framing + async chat.send + real agent.wait.

Bridge's `sendViaGatewayRpcFresh` (agent_dispatcher.dart) matches responses on
`type == 'response' && ref_id == <request id>`, reads `result.run_id` from
chat.send, sends `agent.wait {run_id, timeout}` (seconds, snake_case), and on
wait success uses `result['content']` as the final reply text. Errors carry
`error.retryable` — `false` fails fast, anything else retries the wait.
"""
from __future__ import annotations

from conftest import make_fake_events, shim_server


async def test_chat_send_responds_v1_frame_while_run_in_flight(chat_rpc):
    q, bind = make_fake_events()
    async with shim_server(chat_rpc, bind(chat_rpc)) as client:
        await client.request('abc', 'chat.send', {'message': 'hi'})
        frame = await client.frame_where(lambda f: f.get('type') == 'response')

        assert frame['v'] == 1
        assert frame['ref_id'] == 'abc'
        run_id = frame['result']['run_id']
        assert isinstance(run_id, str) and run_id
        # Run is still in flight — no chat_done was pushed. The response
        # arriving proves chat.send no longer blocks on the agent turn.
        # SSE subscription must be open BEFORE channel_web_chat fires.
        method, params, subscribed_first = chat_rpc.calls[-1]
        assert method == 'openhuman.channel_web_chat'
        assert params['message'] == 'hi'
        assert subscribed_first, 'must subscribe to /events before starting the run'
        q.put_nowait(None)


async def test_agent_wait_returns_content_after_chat_done(chat_rpc):
    q, bind = make_fake_events()
    async with shim_server(chat_rpc, bind(chat_rpc)) as client:
        await client.request('abc', 'chat.send', {'message': 'hi'})
        send_res = await client.frame_where(
            lambda f: f.get('type') == 'response' and f.get('ref_id') == 'abc')
        run_id = send_res['result']['run_id']

        q.put_nowait(('chat_done', {
            'request_id': 'req-1',
            'thread_id': 'codie-bridge',
            'full_response': 'hello world',
        }))

        await client.request('w1', 'agent.wait', {'run_id': run_id, 'timeout': 5})
        wait_res = await client.frame_where(
            lambda f: f.get('type') == 'response' and f.get('ref_id') == 'w1')
        assert wait_res['result']['content'] == 'hello world'
        assert wait_res['result']['status'] == 'ok'


async def test_agent_wait_timeout_is_retryable_error(chat_rpc):
    q, bind = make_fake_events()
    async with shim_server(chat_rpc, bind(chat_rpc)) as client:
        await client.request('abc', 'chat.send', {'message': 'hi'})
        send_res = await client.frame_where(
            lambda f: f.get('type') == 'response' and f.get('ref_id') == 'abc')
        run_id = send_res['result']['run_id']

        # No chat_done pushed — the wait must time out, retryably.
        await client.request('w1', 'agent.wait', {'run_id': run_id, 'timeout': 0})
        wait_res = await client.frame_where(
            lambda f: f.get('type') == 'response' and f.get('ref_id') == 'w1')
        assert wait_res['error']['retryable'] is True
        q.put_nowait(None)


async def test_agent_wait_unknown_run_is_non_retryable_error(chat_rpc):
    q, bind = make_fake_events()
    async with shim_server(chat_rpc, bind(chat_rpc)) as client:
        await client.request('w1', 'agent.wait', {'run_id': 'nope', 'timeout': 1})
        wait_res = await client.frame_where(
            lambda f: f.get('type') == 'response' and f.get('ref_id') == 'w1')
        assert wait_res['error']['retryable'] is False


async def test_health_probe_uses_v1_response_framing(chat_rpc):
    q, bind = make_fake_events()
    async with shim_server(chat_rpc, bind(chat_rpc)) as client:
        await client.request('h1', 'health.probe', {})
        frame = await client.frame_where(
            lambda f: f.get('type') == 'response' and f.get('ref_id') == 'h1')
        assert frame['result']['gateway']['ok'] is True


async def test_chat_history_uses_namespaced_method_and_unwraps(chat_rpc):
    """Core RPC methods are namespaced `openhuman.threads_*` (see /schema);
    the bare `threads.messages_list` is -32601 unknown-method on the live
    core. Its only input is `thread_id` — passing `limit` is rejected with
    "unknown param" (verified live), so limiting happens shim-side. The
    result is the RpcOutcome nesting around an envelope {messages, count}."""
    q, bind = make_fake_events()
    msgs = [{'role': 'user', 'content': f'm{i}'} for i in range(3)]
    chat_rpc.responses['openhuman.threads_messages_list'] = {
        'result': {'messages': msgs, 'count': 3},
        'logs': [],
    }
    async with shim_server(chat_rpc, bind(chat_rpc)) as client:
        await client.request('h2', 'chat.history', {'limit': 2})
        frame = await client.frame_where(
            lambda f: f.get('type') == 'response' and f.get('ref_id') == 'h2')
        assert not frame.get('error')
        # limit applied shim-side, keeping the most recent messages
        assert frame['result']['messages'] == msgs[-2:]
        call = [c for c in chat_rpc.calls
                if c[0] == 'openhuman.threads_messages_list'][0]
        assert call[1] == {'thread_id': 'codie-bridge'}
