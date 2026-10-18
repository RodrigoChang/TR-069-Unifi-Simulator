// Standalone UI regression: node tests/test_dashboard.js (no packages required).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = {};
const context = vm.createContext({
  document: {getElementById: id => elements[id] ||= {}},
  fetch: () => new Promise(() => {}), setInterval: () => {},
});
vm.runInContext(fs.readFileSync('huawei_bridge/static/app.js', 'utf8'), context);
context.snapshot = {aps: [], clients: [], warnings: [], unifi: {}, mesh: {
  clients: [], nodes: ['conflict', 'candidate', 'reported'].map(classification => ({
    classification, manufacturer: classification === 'candidate' ? 'Huawei (probable)' : null,
    reported_role: classification === 'conflict' ? 'agent' : null,
    hostname: '<unsafe>', sources: ['hosts'], online: false,
  })),
}};
vm.runInContext('render(snapshot)', context);
for (const label of ['Identidad/rol en conflicto', 'Candidato mesh',
                     'no confirmado', 'Actividad HostInfo', 'Huawei (probable)', '&lt;unsafe&gt;']) {
  assert.ok(elements.mesh.innerHTML.includes(label), label);
}
assert.ok(!elements.mesh.innerHTML.includes('<unsafe>'));
console.log('Dashboard classification regression passed');
