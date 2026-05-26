import fs from 'node:fs';
import readline from 'node:readline';

function reply(obj) {
  process.stderr.write(JSON.stringify(obj) + '\n');
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on('line', (line) => {
  if (!line.trim()) return;
  const req = JSON.parse(line);
  fs.appendFileSync('probe-requests.jsonl', JSON.stringify(req) + '\n');
  if (req.method === 'getManifest') {
    reply({ jsonrpc: '2.0', id: req.id, result: { manifest: { prettyName: 'probe-generator', defaultOutput: './out' } } });
  } else if (req.method === 'generate') {
    fs.writeFileSync('probe-generate-params.json', JSON.stringify(req.params, null, 2));
    reply({ jsonrpc: '2.0', id: req.id, result: null });
  } else {
    reply({ jsonrpc: '2.0', id: req.id, error: { code: -32601, message: 'unknown method', data: {} } });
  }
});
