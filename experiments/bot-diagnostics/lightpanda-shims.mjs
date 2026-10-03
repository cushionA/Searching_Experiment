// Add only the missing table insertion methods in the page's own DOM realm.
export function installTableCompatibility() {
  const view = globalThis;
  if (!view.document) return;

  const defineMethod = (ctor, name, fn) => {
    if (ctor?.prototype && typeof ctor.prototype[name] !== 'function') {
      Object.defineProperty(ctor.prototype, name, { configurable: true, writable: true, value: fn });
    }
  };
  const directRows = (parent) => Array.from(parent.children || []).filter((el) => el.localName === 'tr');
  const sectionRows = (parent) => directRows(parent);
  // HTMLTableElement.rows follows table section order, excluding nested tables.
  const tableRows = (table) => {
    const children = Array.from(table.children || []);
    const rows = [];
    for (const child of children) if (child.localName === 'thead') rows.push(...directRows(child));
    for (const child of children) {
      if (child.localName === 'tr') rows.push(child);
      else if (child.localName === 'tbody') rows.push(...directRows(child));
    }
    for (const child of children) if (child.localName === 'tfoot') rows.push(...directRows(child));
    return rows;
  };
  const toLong = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number) || number === 0) return 0;
    const integer = Math.trunc(number);
    const unsigned = ((integer % 4294967296) + 4294967296) % 4294967296;
    return unsigned >= 2147483648 ? unsigned - 4294967296 : unsigned;
  };
  const outOfRange = (document) => {
    const Exception = document.defaultView?.DOMException || view.DOMException;
    if (Exception) throw new Exception('The index is out of range.', 'IndexSizeError');
    const error = new Error('The index is out of range.');
    error.name = 'IndexSizeError';
    throw error;
  };
  const checkIndex = (index, length, document) => {
    if (index !== -1 && (index < 0 || index > length)) outOfRange(document);
  };
  const insertAt = (parent, rows, index, document) => {
    const row = document.createElement('tr');
    if (index === -1 || index === rows.length) parent.appendChild(row);
    else parent.insertBefore(row, rows[index]);
    return row;
  };

  defineMethod(view.HTMLTableSectionElement, 'insertRow', function (index = -1) {
    const document = this.ownerDocument;
    index = toLong(index);
    const rows = sectionRows(this);
    checkIndex(index, rows.length, document);
    return insertAt(this, rows, index, document);
  });
  defineMethod(view.HTMLTableRowElement, 'insertCell', function (index = -1) {
    const document = this.ownerDocument;
    index = toLong(index);
    const cells = Array.from(this.children || []).filter((el) => el.localName === 'td' || el.localName === 'th');
    checkIndex(index, cells.length, document);
    const cell = document.createElement('td');
    if (index === -1 || index === cells.length) this.appendChild(cell);
    else this.insertBefore(cell, cells[index]);
    return cell;
  });
  defineMethod(view.HTMLTableElement, 'insertRow', function (index = -1) {
    const document = this.ownerDocument;
    index = toLong(index);
    const rows = tableRows(this);
    checkIndex(index, rows.length, document);
    if (rows.length && index !== -1 && index < rows.length) {
      const row = document.createElement('tr');
      rows[index].parentNode.insertBefore(row, rows[index]);
      return row;
    }
    if (rows.length) return insertAt(rows.at(-1).parentNode, directRows(rows.at(-1).parentNode), -1, document);

    const sections = Array.from(this.children || []).filter((el) => el.localName === 'tbody');
    if (sections.length) return insertAt(sections.at(-1), [], -1, document);
    const section = document.createElement('tbody');
    this.appendChild(section);
    return insertAt(section, [], -1, document);
  });
}

export function installObservationProbe() {
  const doc = globalThis.document;
  if (!doc) return;
  const mark = () => doc.documentElement?.setAttribute('data-lightpanda-probe', 'loaded');
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', mark, { once: true });
  else mark();
}
