Claude v9 fixed-pair gates are vendored here as a private package namespace.
The three upstream modules are adapted to relative imports and to resolve raw
source paths through the caller's RawStore root. No top-level sku_gates,
sku_gate_atoms, or sku_gate_sources module is imported or cached.

sku_integrated_gate_v1.predict_case runs method A on one selected Rakuten SKU
and the complete fixed AU SKU array, using full_spec_notice. It returns accept
only when one row is fully supported and every other AU row is explicitly
conflicting. Unknown or unproven rows drop.
Page specification differences are retained as notices. Price, stock, and
other transactional fields are excluded from identity.

This adapter is source-only. It retains both complete raw gate results so a
later evidence-completion layer can inspect them without changing this gate.
The dimension parser keeps slash-separated alternatives attached to their
immediately preceding labeled dimension; all quoted text and offsets include
the complete alternative expression.

When a RawStore is supplied, every adopted requirement/support citation and
at least one explicit, verified conflict citation for each excluded AU row
must resolve to the original source. A page-spec notice alone cannot exclude
a competing row. Closed-contents sums cite their component lines as a span
list, and sibling-option conflicts cite the literal option value in the
Rakuten selector list.
