import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import {
  mkdtempSync,
  readdirSync,
  readFileSync,
  writeFileSync,
  mkdirSync,
  rmSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createTemplateRenderer } from 'nunjitsu';
import { parseAllDocuments } from 'yaml';

const output = mkdtempSync(join(tmpdir(), 'backstage-service-'));
const clusterOutput = mkdtempSync(join(tmpdir(), 'backstage-cluster-'));
const renderer = createTemplateRenderer({});
const context = renderer.prepareContext({
  values: { name: 'example-service', owner: 'group:default/platform-team' },
});

function renderTree(source, destination, values = context) {
  mkdirSync(destination, { recursive: true });
  for (const file of readdirSync(source, { withFileTypes: true })) {
    const from = join(source, file.name);
    const to = join(destination, file.name);
    if (file.isDirectory()) renderTree(from, to, values);
    else writeFileSync(to, renderer.render(readFileSync(from, 'utf8'), values));
  }
}

try {
  renderTree('templates/service/skeleton', output);
  execFileSync(process.env.PYTHON ?? 'python3', ['-m', 'unittest', '-v'], {
    cwd: output,
    stdio: 'inherit',
  });
  const documents = parseAllDocuments(
    readFileSync(join(output, 'deployment.yaml'), 'utf8'),
  );
  assert(documents.every(document => document.errors.length === 0));
  const resources = documents.map(document => document.toJS());
  assert.deepEqual(
    resources.map(resource => resource.kind),
    ['Deployment', 'Service', 'NetworkPolicy'],
  );
  const deployment = resources[0].spec.template;
  assert.equal(
    resources[1].spec.selector['app.kubernetes.io/name'],
    deployment.metadata.labels['app.kubernetes.io/name'],
  );
  assert.equal(
    deployment.spec.containers[0].readinessProbe.httpGet.path,
    '/healthz',
  );
  assert.equal(deployment.spec.automountServiceAccountToken, false);
  assert.deepEqual(resources[2].spec.egress, []);
  console.log(
    'Generated service HTTP behaviour and deployment contract passed',
  );

  const clusterContext = renderer.prepareContext({
    values: {
      name: 'aks-example-dev',
      location: 'uksouth',
      kubernetesVersion: '1.35',
      networkCidr: '10.32.0.0/16',
      systemSubnetCidr: '10.32.0.0/24',
      applicationSubnetCidr: '10.32.1.0/24',
      adminGroupObjectId: '11111111-1111-1111-1111-111111111111',
      apiAllowlistSecret: 'aks-api-ranges',
      stateResourceGroup: 'rg-state',
      stateAccount: 'examplestate',
      stateContainer: 'tfstate',
      keyVaultResourceGroup: 'rg-platform',
      keyVaultName: 'examplevault',
      managementVnetName: 'vnet-management',
      managementVnetResourceGroup: 'rg-management',
      registryName: 'exampleacr',
      registryResourceGroup: 'rg-registry',
      fleetId: '',
      systemVmSize: 'Standard_D4s_v4',
      platformVmSize: 'Standard_D4ds_v4',
      servicesVmSize: 'Standard_D4ds_v6',
    },
  });
  renderTree('templates/cluster/skeleton', clusterOutput, clusterContext);
  renderTree(
    'templates/cluster/profile',
    join(clusterOutput, 'gitops-profile'),
    clusterContext,
  );
  execFileSync('terraform', ['fmt', '-check'], {
    cwd: clusterOutput,
    stdio: 'inherit',
  });
  execFileSync('terraform', ['init', '-backend=false', '-input=false'], {
    cwd: clusterOutput,
    stdio: 'inherit',
  });
  execFileSync('terraform', ['validate'], {
    cwd: clusterOutput,
    stdio: 'inherit',
  });
  assert.match(
    readFileSync(join(clusterOutput, 'backend.tf'), 'utf8'),
    /clusters\/aks-example-dev\.tfstate/,
  );
  assert.match(
    readFileSync(join(clusterOutput, 'terraform.tfvars'), 'utf8'),
    /fleet_id\s*= ""/,
  );
  assert.match(
    readFileSync(join(clusterOutput, 'terraform.tfvars'), 'utf8'),
    /services\s*=\s*\{\s*vm_size\s*= "Standard_D4ds_v6"/,
  );
  assert.match(
    readFileSync(join(clusterOutput, 'gitops-profile/profile.yaml'), 'utf8'),
    /name: aks-example-dev/,
  );
  console.log('Generated cluster root is formatted with a unique backend key');
} finally {
  rmSync(output, { recursive: true, force: true });
  rmSync(clusterOutput, { recursive: true, force: true });
}
