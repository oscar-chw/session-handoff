import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'handoff', Path(__file__).resolve().parents[1] / 'handoff.py')
handoff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handoff)


def message(role, content, uid='example'):
    return {'type': role, 'uuid': uid,
            'message': {'role': role, 'content': content}}


class ProjectionTests(unittest.TestCase):
    def test_groups_prose_in_order_and_keeps_tools_out_of_chat(self):
        source = [message('user', 'Please revise.'),
                  message('assistant', [{'type': 'text', 'text': 'First.'},
                                        {'type': 'tool_use',
                                         'input': {'x': 'private'}}]),
                  message('user', [{'type': 'tool_result', 'content': 'long result'}]),
                  message('assistant', [{'type': 'text', 'text': 'Done. 中文'}]),
                  message('user', 'Next.'), message('assistant', 'Second reply.')]
        result = handoff.project(source)
        self.assertEqual([(r['role'], r['text']) for r in result], [
            ('user', 'Please revise.'), ('assistant', 'First.\n\nDone. 中文'),
            ('user', 'Next.'), ('assistant', 'Second reply.')])

    def test_rejects_no_conversation(self):
        with self.assertRaises(ValueError):
            handoff.project([{'type': 'system'}])

    def test_metadata_is_preserved_without_becoming_a_new_user_request(self):
        meta = message('user', 'Context from another session.')
        meta['is_meta'] = True
        result = handoff.project([message('user', 'Do this.'),
                                  message('assistant', 'Working.'), meta,
                                  message('assistant', 'Finished.')])
        self.assertEqual([r['role'] for r in result], ['user', 'assistant'])
        self.assertIn('Context from another session.', result[1]['text'])
        self.assertIn('not a new user request', result[1]['text'])

    def test_image_only_user_turn_still_has_a_visible_placeholder(self):
        result = handoff.project([message('user', [
            {'type': 'image', 'source': {'data': 'not-real-image'}}]),
            message('assistant', 'I can see it.')])
        self.assertEqual(result[0]['role'], 'user')
        self.assertIn('archiv', result[0]['text'].lower())
        self.assertNotIn('not-real-image', result[0]['text'])


class BundleTests(unittest.TestCase):
    def test_source_flags_preserve_compaction_and_user_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.jsonl'
            source.write_text(json.dumps(message('user', 'Compaction', 'u1')) + '\n')
            active = root / 'active.json'
            summary = message('user', 'Compaction', 'u1')
            summary['is_meta'] = True
            summary['isMeta'] = True
            active.write_text(json.dumps([summary, message('assistant', 'Reply')]))
            handoff.bundle(source, active, root / 'bundle', str(root), 'Example')
            rows = [json.loads(line) for line in
                    (root / 'bundle/conversation.jsonl').read_text().splitlines()]
            self.assertEqual(rows[0]['type'], 'user')
            self.assertEqual(rows[0]['message']['content'], 'Compaction')

    def test_projection_and_archive_use_the_same_active_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.jsonl'
            source.write_text('{"type":"fixture"}\n')
            active = root / 'active.json'
            original = json.dumps([message('user', 'Before')]).encode()
            active.write_bytes(original)
            read = Path.read_bytes

            def concurrent_change(path):
                data = read(path)
                if path == active:
                    active.write_text(json.dumps([message('user', 'After')]))
                return data

            with patch.object(Path, 'read_bytes', concurrent_change):
                handoff.bundle(source, active, root / 'bundle', str(root), 'Example')
            self.assertEqual((root / 'bundle/active.json').read_bytes(), original)
            lines = (root / 'bundle/conversation.jsonl').read_text().splitlines()
            row = json.loads(lines[0])
            self.assertEqual(row['message']['content'], 'Before')

    def test_preserves_sources_and_refuses_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / 'source.jsonl'
            raw.write_bytes(b'{"opaque":"preserve every byte"}\n')
            active = root / 'active.json'
            active.write_text(json.dumps([message('user', 'Hello'),
                                          message('assistant', 'Hi')]))
            target = root / 'bundle'
            manifest = handoff.bundle(raw, active, target, str(root), 'Example')
            self.assertEqual((target / 'source.jsonl').read_bytes(), raw.read_bytes())
            self.assertEqual((target / 'active.json').read_bytes(), active.read_bytes())
            self.assertEqual(manifest['source_sha256'],
                             hashlib.sha256(raw.read_bytes()).hexdigest())
            rows = [json.loads(line) for line in
                    (target / 'conversation.jsonl').read_text().splitlines()]
            self.assertEqual(rows[0]['message']['content'], 'Hello')
            self.assertEqual(rows[1]['message']['content'], 'Hi')
            self.assertEqual(rows[1]['parentUuid'], rows[0]['uuid'])
            with self.assertRaises(FileExistsError):
                handoff.bundle(raw, active, target, str(root), 'Example')


class ReturnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / 'claude-copy.jsonl'
        self.prefix = (json.dumps({'type': 'assistant', 'uuid': 'leaf',
                                  'sessionId': 'copied-session',
                                  'message': {'role': 'assistant',
                                              'content': 'Before'}}) + '\n').encode()
        self.target.write_bytes(self.prefix)
        self.checkpoint = self.root / 'checkpoint'
        handoff.checkpoint(self.target, self.checkpoint, 'leaf',
                           'copied-session', str(self.root))

    def turn(self, identifier='t1'):
        return {'id': identifier, 'status': 'completed', 'items': [
            {'type': 'userMessage', 'content': [{'type': 'text', 'text': 'Request'}]},
            {'type': 'agentMessage', 'text': 'Reply 中文'}]}

    def test_multiple_turns_are_exact_and_repeat_is_a_noop(self):
        first = handoff.append_turn(self.checkpoint, self.turn())
        data = self.target.read_bytes()
        repeat = handoff.append_turn(self.checkpoint, self.turn())
        self.assertEqual(self.target.read_bytes(), data)
        self.assertEqual(repeat['action'], 'already-returned')
        second = handoff.append_turn(self.checkpoint, self.turn('t2'))
        rows = [json.loads(line) for line in self.target.read_bytes().splitlines()]
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[1]['message']['content'], 'Request')
        self.assertEqual(rows[2]['message']['content'][0]['text'], 'Reply 中文')
        self.assertEqual(rows[4]['parentUuid'], first['leaf'])
        self.assertEqual(rows[5]['uuid'], second['leaf'])
        self.assertTrue(self.target.read_bytes().startswith(self.prefix))
        self.assertEqual((self.checkpoint / 'turns/t1.json').read_text(),
                         json.dumps(self.turn(), ensure_ascii=False, indent=2))

    def test_destination_divergence_is_rejected_without_changing_it(self):
        self.target.write_bytes(self.prefix + b'{"foreign":"change"}\n')
        before = self.target.read_bytes()
        with self.assertRaises(ValueError):
            handoff.append_turn(self.checkpoint, self.turn())
        self.assertEqual(self.target.read_bytes(), before)

    def test_incomplete_turn_is_rejected(self):
        turn = self.turn()
        turn['status'] = 'inProgress'
        with self.assertRaises(ValueError):
            handoff.append_turn(self.checkpoint, turn)
        self.assertEqual(self.target.read_bytes(), self.prefix)

    def test_plain_string_user_content_is_supported(self):
        turn = self.turn()
        turn['items'][0]['content'] = 'Plain text'
        handoff.append_turn(self.checkpoint, turn)
        rows = [json.loads(line) for line in self.target.read_bytes().splitlines()]
        self.assertEqual(rows[1]['message']['content'], 'Plain text')

    def test_reused_turn_id_with_different_content_is_rejected(self):
        handoff.append_turn(self.checkpoint, self.turn())
        before = self.target.read_bytes()
        turn = self.turn()
        turn['items'][-1]['text'] = 'Different'
        with self.assertRaises(ValueError):
            handoff.append_turn(self.checkpoint, turn)
        self.assertEqual(self.target.read_bytes(), before)

    def test_tool_records_are_archived_without_becoming_executable_calls(self):
        turn = self.turn()
        turn['items'].insert(1, {'type': 'commandExecution', 'command': 'example'})
        handoff.append_turn(self.checkpoint, turn)
        rows = [json.loads(line) for line in self.target.read_bytes().splitlines()]
        self.assertIn('archiv', rows[1]['message']['content'].lower())
        self.assertNotIn('tool_use', self.target.read_text())
        archived = (self.checkpoint / 'turns/t1.json').read_text()
        self.assertIn('commandExecution', archived)

    def test_retry_after_committed_state_clears_its_pending_journal(self):
        transaction = handoff.prepare_turn(self.checkpoint, self.turn())
        handoff.append_turn(self.checkpoint, self.turn())
        (self.checkpoint / 'pending.json').write_text(json.dumps(transaction))
        handoff.append_turn(self.checkpoint, self.turn())
        self.assertFalse((self.checkpoint / 'pending.json').exists())
        handoff.append_turn(self.checkpoint, self.turn('t2'))

    def test_partial_append_recovers_from_prepared_transaction(self):
        # Simulate interruption after journal creation and a partial history write.
        prepared = handoff.prepare_turn(self.checkpoint, self.turn())
        suffix = prepared['suffix'].encode()
        self.target.write_bytes(self.prefix + suffix[:len(suffix) // 2])
        result = handoff.append_turn(self.checkpoint, self.turn())
        self.assertEqual(self.target.read_bytes(), self.prefix + suffix)
        self.assertEqual(result['action'], 'recovered')

    def test_wrong_partial_suffix_is_not_repaired(self):
        handoff.prepare_turn(self.checkpoint, self.turn())
        before = self.prefix + b'not-the-prepared-suffix'
        self.target.write_bytes(before)
        with self.assertRaises(ValueError):
            handoff.append_turn(self.checkpoint, self.turn())
        self.assertEqual(self.target.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
