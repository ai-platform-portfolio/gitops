import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readdirSync, readFileSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createTemplateRenderer } from 'nunjitsu';
import { parseAllDocuments } from 'yaml';

const output = mkdtempSync(join(tmpdir(), 'backstage-service-'));
const renderer = createTemplateRenderer({});
const context = renderer.prepareContext({
  values: { name: 'example-service', owner: 'group:default/platform-team' },
});

function renderTree(source, destination) {
  mkdirSync(destination, { recursive: true });
  for (const file of readdirSync(source, { withFileTypes: true })) {
    const from = join(source, file.name);
    const to = join(destination, file.name);
    if (file.isDirectory()) renderTree(from, to);
    else writeFileSync(to, renderer.render(readFileSync(from, 'utf8'), context));
  }
}

try {
  renderTree('templates/service/skeleton', output);
  execFileSync(process.env.PYTHON ?? 'python3', ['-m', 'unittest', '-v'], {
    cwd: output, stdio: 'inherit',
  });
  const documents = parseAllDocuments(readFileSync(join(output, 'deployment.yaml'), 'utf8'));
  assert(documents.every(document => document.errors.length === 0));
  const resources = documents.map(document => document.toJS());
  assert.deepEqual(resources.map(resource => resource.kind), ['Deployment', 'Service', 'NetworkPolicy']);
  const deployment = resources[0].spec.template;
  assert.equal(resources[1].spec.selector['app.kubernetes.io/name'], deployment.metadata.labels['app.kubernetes.io/name']);
  assert.equal(deployment.spec.containers[0].readinessProbe.httpGet.path, '/healthz');
  assert.equal(deployment.spec.automountServiceAccountToken, false);
  assert.deepEqual(resources[2].spec.egress, []);
  console.log('Generated service HTTP behaviour and deployment contract passed');
} finally {
  rmSync(output, { recursive: true, force: true });
}
