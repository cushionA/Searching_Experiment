// Pass this exported, self-contained function to page.evaluate after installing
// the shims in that page. It intentionally uses a real browser DOM fixture.
export function tableContract() {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const expectIndexError = (action) => {
    try { action(); } catch (error) {
      assert(error.name === 'IndexSizeError', `expected IndexSizeError, got ${error.name}`);
      return;
    }
    throw new Error('expected IndexSizeError');
  };
  const doc = document;
  const rows = (section) => Array.from(section.children).filter((child) => child.localName === 'tr');
  const cells = (row) => Array.from(row.children).filter((child) => child.localName === 'td' || child.localName === 'th');
  const table = doc.createElement('table');
  const head = doc.createElement('thead');
  const bodyA = doc.createElement('tbody');
  const bodyB = doc.createElement('tbody');
  const foot = doc.createElement('tfoot');
  const headRow = head.insertRow();
  const rowA = bodyA.insertRow();
  const rowB = bodyB.insertRow();
  const footRow = foot.insertRow();
  table.append(foot, bodyA, bodyB, head); // Section ordering differs from DOM order.

  assert(table.insertRow(1).parentNode === bodyA, 'table.insertRow(index) must follow table row order');
  const appended = table.insertRow(-1);
  assert(appended.parentNode === foot, 'table.insertRow(-1) must append after the final row');
  assert(table.insertRow().parentNode === foot, 'omitted table row index must append');
  const firstInA = bodyA.firstElementChild;
  assert(bodyA.insertRow(0).nextElementSibling === firstInA, 'section insertion must honor index');

  const cell = rowA.insertCell(-1);
  assert(cell.localName === 'td' && cell.parentNode === rowA, 'insertCell(-1) must append a td');
  const cellAtZero = rowA.insertCell(0);
  assert(cellAtZero.nextElementSibling === cell, 'insertCell(index) must honor index');
  expectIndexError(() => bodyA.insertRow(rows(bodyA).length + 1));
  expectIndexError(() => rowA.insertCell(cells(rowA).length + 1));
  expectIndexError(() => table.insertRow(100));
  expectIndexError(() => bodyA.insertRow(-2));
  bodyA.insertRow(NaN); // WebIDL long conversion maps NaN to zero.
  bodyA.insertRow(Infinity); // Ditto for infinities.

  const inner = doc.createElement('table');
  const innerBody = doc.createElement('tbody');
  const innerRow = innerBody.insertRow();
  inner.appendChild(innerBody);
  const outer = doc.createElement('table');
  const outerBody = doc.createElement('tbody');
  const outerRow = outerBody.insertRow();
  outerRow.appendChild(inner);
  outer.appendChild(outerBody);
  outer.insertRow(-1);
  assert(rows(outerBody).length === 2 && rows(innerBody).length === 1, 'nested table rows must not enter outer row order');
  const empty = doc.createElement('table');
  assert(empty.insertRow().parentNode.localName === 'tbody', 'an empty table must create a tbody');
  const emptyFooter = doc.createElement('table');
  const footerOnly = emptyFooter.appendChild(doc.createElement('tfoot'));
  assert(emptyFooter.insertRow().parentNode.previousElementSibling === footerOnly, 'new tbody must append to an empty table');

  assert(headRow.parentNode === head && rowB.parentNode === bodyB && footRow.parentNode === foot,
    'fixture rows must remain in their original sections');
  return { ok: true };
}
