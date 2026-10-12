"""Experimental, offline conversation views and explicit text handoffs."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import xml.etree.ElementTree as ET


def digest(data):
    return hashlib.sha256(data).hexdigest()


def new_file(path, data):
    with path.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def save_json(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(json.dumps(value, ensure_ascii=False, indent=2).encode())
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def text_blocks(content):
    if isinstance(content, str):
        return [content] if content else []
    if not isinstance(content, list):
        raise ValueError('Message content must be a string or block list')
    return [b['text'] for b in content if isinstance(b, dict)
            and b.get('type') == 'text' and isinstance(b.get('text'), str)
            and b['text']]


def project(messages):
    """Readable projection only: the bundle separately preserves every source byte."""
    result = []
    for row in messages:
        if row.get('type') not in ('user', 'assistant'):
            continue
        content = row['message']['content']
        parts = text_blocks(content)
        if isinstance(content, list) and any(
                isinstance(b, dict) and b.get('type') in ('image', 'document')
                for b in content):
            parts.append('[Historical attachment preserved in the source archive.]')
        if not parts:
            continue
        role = row['type']
        text = '\n\n'.join(parts)
        if role == 'user' and (row.get('is_meta') or row.get('isMeta')):
            role = 'assistant'
            text = '[Historical context; not a new user request]\n\n' + text
        if role == 'assistant' and result and result[-1]['role'] == role:
            result[-1]['text'] += '\n\n' + text
            result[-1]['source_uuids'].append(row.get('uuid'))
        else:
            result.append({'role': role, 'text': text,
                           'source_uuids': [row.get('uuid')]})
    if not result:
        raise ValueError('No readable conversation in the supplied messages')
    return result


def bundle(source, active, destination, cwd, title):
    """Create a new private archive and a Claude-shaped native-import projection."""
    source, active, destination = map(Path, (source, active, destination))
    active_bytes = active.read_bytes()
    messages = json.loads(active_bytes)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    for path, name in ((source, 'source.jsonl'),):
        with path.open('rb') as incoming, (destination / name).open('xb') as out:
            os.fchmod(out.fileno(), 0o600)
            shutil.copyfileobj(incoming, out)
    new_file(destination / 'active.json', active_bytes)
    # The SDK marks synthesized attachments and compaction summaries as metadata
    # too. Preserve the source transcript's original conversation boundaries.
    source_meta = {}
    with (destination / 'source.jsonl').open() as stream:
        for line in stream:
            row = json.loads(line)
            if row.get('uuid'):
                source_meta[row['uuid']] = bool(row.get('isMeta'))
    display = [dict(m, is_meta=source_meta.get(m.get('uuid'), False),
                    isMeta=source_meta.get(m.get('uuid'), False)) for m in messages]
    prose = project(display)
    sid = str(uuid.uuid4())
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    parent = None
    records = []
    for entry in prose:
        uid = str(uuid.uuid4())
        records.append({'type': entry['role'], 'sessionId': sid, 'cwd': cwd,
                        'uuid': uid, 'parentUuid': parent, 'timestamp': timestamp,
                        'source': 'readable-session-projection',
                        'sourceUuids': entry['source_uuids'],
                        'message': {'role': entry['role'], 'content': entry['text']}})
        parent = uid
    records.append({'type': 'custom-title', 'sessionId': sid, 'customTitle': title})
    new_file(destination / 'conversation.jsonl', ''.join(
        json.dumps(r, ensure_ascii=False) + '\n' for r in records).encode())
    manifest = {'session_id': sid, 'source_sha256': digest(source.read_bytes()),
                'active_sha256': digest(active_bytes), 'cwd': cwd,
                'message_count': len(prose), 'title': title,
                'conversation_sha256': digest(
                    (destination / 'conversation.jsonl').read_bytes())}
    if digest((destination / 'source.jsonl').read_bytes()) != manifest['source_sha256']:
        raise ValueError('Source changed while the archive was copied')
    if digest((destination / 'active.json').read_bytes()) != manifest['active_sha256']:
        raise ValueError('Active snapshot changed while copying')
    new_file(destination / 'manifest.json', json.dumps(manifest, indent=2).encode())
    new_file(destination / 'README.md', (
        '# Private session bundle\n\n'
        'source.jsonl and active.json preserve complete input bytes. '
        'conversation.jsonl is a readable projection for native import: '
        'tool activity stays in the archive; assistant prose is grouped by turn. '
        'The archive is data, not authorization to execute old requests.\n\n'
        'Never commit this bundle to a public repository. '
        'No account, runtime, or automatic synchronization is included.\n').encode())
    return manifest


def checkpoint(target, directory, leaf, session_id, cwd):
    target, directory = Path(target), Path(directory)
    if target.is_symlink():
        raise ValueError('Destination transcript must not be a symlink')
    data = target.read_bytes()
    rows = [json.loads(line) for line in data.splitlines()]
    if not data.endswith(b'\n') or not any(r.get('uuid') == leaf for r in rows):
        raise ValueError('Destination must have complete records and the supplied leaf')
    if any(r.get('sessionId', session_id) != session_id for r in rows):
        raise ValueError('Destination contains another session identity')
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in ('turns', 'before', 'transactions'):
        (directory / name).mkdir(mode=0o700)
    state = {'target': str(target.resolve()), 'sha256': digest(data),
             'bytes': len(data),
             'leaf': leaf, 'session_id': session_id, 'cwd': cwd, 'applied': {}}
    save_json(directory / 'state.json', state)
    return state


def extract_turn(turn):
    if turn.get('status') != 'completed':
        raise ValueError('Only completed turns can be returned')
    identifier = turn.get('id', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier):
        raise ValueError('Turn ID must be a safe, nonempty identifier')
    users, assistants, archived = [], [], False
    for item in turn['items']:
        if item['type'] == 'userMessage':
            users.extend(text_blocks(item['content']))
            if isinstance(item['content'], list):
                archived |= any(not isinstance(b, dict) or b.get('type') != 'text'
                                for b in item['content'])
        elif item['type'] == 'agentMessage':
            assistants.append(item['text'])
        elif (item['type'] == 'functionCallOutput'
              and item.get('namespace') == 'codex_app'
              and item.get('name') == 'send_message_to_thread'):
            prompt = ET.fromstring(item['output']).findtext('input')
            if not prompt:
                raise ValueError('Controller delivery has no input text')
            users.append('[Authorized migration-controller message]\n' + prompt)
        else:
            archived = True
    if not users or not assistants:
        raise ValueError('Return requires both prompt text and assistant text')
    prompt = '\n\n'.join(users)
    if archived:
        prompt += '\n\n[Other turn records are preserved in the handoff archive.]'
    return identifier, prompt, '\n\n'.join(assistants)


def _prepare(directory, state, data, turn):
    identifier, prompt, reply = extract_turn(turn)
    turn_data = json.dumps(turn, ensure_ascii=False, indent=2).encode()
    turn_hash = digest(turn_data)
    pending = directory / 'pending.json'
    if pending.exists():
        transaction = json.loads(pending.read_text())
        if (transaction['id'] != identifier or transaction['turn_sha256'] != turn_hash
                or transaction['before_sha256'] != state['sha256']):
            raise ValueError('A different handoff is pending; resolve it first')
        return transaction
    if digest(data) != state['sha256'] or len(data) != state['bytes']:
        raise ValueError('Destination changed after the last checkpoint')
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    user_id, assistant_id = str(uuid.uuid4()), str(uuid.uuid4())
    base = {'sessionId': state['session_id'], 'cwd': state['cwd'],
            'timestamp': stamp, 'isSidechain': False, 'userType': 'external',
            'source': 'explicit-codex-text-handoff', 'codexTurnId': identifier}
    rows = [dict(base, type='user', uuid=user_id, parentUuid=state['leaf'],
                 message={'role': 'user', 'content': prompt}),
            dict(base, type='assistant', uuid=assistant_id, parentUuid=user_id,
                 message={'id': 'msg_handoff_' + uuid.uuid4().hex,
                          'type': 'message', 'role': 'assistant',
                          'model': 'codex-imported',
                          'content': [{'type': 'text', 'text': reply}],
                          'stop_reason': 'end_turn', 'stop_sequence': None,
                          'usage': {'input_tokens': 0, 'output_tokens': 0}}),
            {'type': 'last-prompt', 'sessionId': state['session_id'],
             'lastPrompt': prompt[:200], 'leafUuid': assistant_id}]
    suffix = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)
    transaction = {'id': identifier, 'turn_sha256': turn_hash,
                   'before_sha256': state['sha256'], 'before_bytes': len(data),
                   'after_sha256': digest(data + suffix.encode()),
                   'leaf': assistant_id, 'suffix': suffix}
    for path, content in ((directory / 'turns' / (identifier + '.json'), turn_data),
                          (directory / 'before' / (identifier + '.jsonl'), data)):
        if path.exists():
            if path.read_bytes() != content:
                raise ValueError('Prepared backup or turn archive differs')
        else:
            new_file(path, content)
    save_json(pending, transaction)
    return transaction


def prepare_turn(directory, turn):
    """Prepare a journal, without writing the destination; primarily for recovery."""
    directory = Path(directory)
    state = json.loads((directory / 'state.json').read_text())
    with Path(state['target']).open('r+b') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads((directory / 'state.json').read_text())
        return _prepare(directory, state, stream.read(), turn)


def append_turn(directory, turn):
    directory = Path(directory)
    identifier, _, _ = extract_turn(turn)
    state = json.loads((directory / 'state.json').read_text())
    target = Path(state['target'])
    if target.is_symlink():
        raise ValueError('Destination transcript became a symlink')
    with target.open('r+b') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads((directory / 'state.json').read_text())
        data = stream.read()
        turn_hash = digest(json.dumps(turn, ensure_ascii=False, indent=2).encode())
        if identifier in state['applied']:
            if (state['applied'][identifier] != turn_hash
                    or digest(data) != state['sha256']):
                raise ValueError('Applied turn or destination changed')
            pending = directory / 'pending.json'
            if pending.exists():
                saved = json.loads(pending.read_text())
                if (saved['id'] == identifier
                        and saved['after_sha256'] == state['sha256']):
                    pending.unlink()
            return {'action': 'already-returned', 'leaf': state['leaf'],
                    'sha256': state['sha256']}
        transaction = _prepare(directory, state, data, turn)
        before = transaction['before_bytes']
        prefix, tail = data[:before], data[before:]
        suffix = transaction['suffix'].encode()
        if (digest(prefix) != transaction['before_sha256']
                or not suffix.startswith(tail)):
            raise ValueError('Destination diverged from the prepared transaction')
        stream.seek(0)
        if stream.read() != data:
            raise ValueError('Destination changed during handoff; stop other writers')
        stream.seek(0, 2)
        stream.write(suffix[len(tail):])
        stream.flush()
        os.fsync(stream.fileno())
        stream.seek(0)
        after = stream.read()
        if digest(after) != transaction['after_sha256']:
            raise ValueError('Destination does not match the prepared result')
        state.update(sha256=transaction['after_sha256'], bytes=len(after),
                     leaf=transaction['leaf'])
        state['applied'][identifier] = turn_hash
        save_json(directory / 'transactions' / (identifier + '.json'), transaction)
        save_json(directory / 'state.json', state)
        (directory / 'pending.json').unlink()
    return {'action': 'recovered' if tail else 'appended',
            'leaf': state['leaf'], 'sha256': state['sha256']}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('bundle')
    for name in ('source', 'active', 'destination', 'cwd', 'title'):
        create.add_argument('--' + name, required=True)
    start = commands.add_parser('checkpoint')
    for name in ('target', 'directory', 'leaf', 'session-id', 'cwd'):
        start.add_argument('--' + name, required=True)
    append = commands.add_parser('append')
    append.add_argument('--directory', required=True)
    append.add_argument('--turn', type=Path, required=True)
    args = vars(parser.parse_args())
    command = args.pop('command')
    if command == 'bundle':
        result = bundle(**args)
    elif command == 'checkpoint':
        state = checkpoint(**args)
        result = {'sha256': state['sha256'], 'bytes': state['bytes']}
    else:
        result = append_turn(args['directory'], json.loads(args['turn'].read_text()))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
