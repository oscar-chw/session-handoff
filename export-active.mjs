// Uses Anthropic's offline session reader; makes no model request.
import { getSessionMessages } from '@anthropic-ai/claude-agent-sdk';
import { writeFile } from 'node:fs/promises';

const [sessionId, projectDirectory, output] = process.argv.slice(2);
if (!sessionId || !projectDirectory || !output) {
  throw new Error('Usage: node export-active.mjs SESSION_ID PROJECT_DIRECTORY OUTPUT.json');
}
const messages = await getSessionMessages(sessionId, { dir: projectDirectory });
if (!messages.length) throw new Error('No resumable messages found for that session');
await writeFile(output, JSON.stringify(messages, null, 2), { flag: 'wx', mode: 0o600 });
console.log(JSON.stringify({ messages: messages.length, output }));
