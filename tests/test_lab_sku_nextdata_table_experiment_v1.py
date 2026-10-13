from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'experiments/sku-matching'))
import run_nextdata_table_experiment_v1 as EXP


class NextdataExperimentTests(unittest.TestCase):
    def test_table_citations_can_only_remap_from_supplied_subset(self):
        pair = {'pair_id': 'p', 'cache_key': 'c', 'option_values': ['綿'],
                'candidate_vector': [{'option_evidence_id': 'e'}]}
        text = '材質 綿'
        block = {'block_id': 'table-b', 'source_sha256': 'b' * 64,
                 'source_alias': 'body-source:aaaaaaaaaaaaaaaa:1', 'support_text': text}
        full = {'pair_id': 'p', 'cache_key': 'c', 'candidate_vector': pair['candidate_vector'],
                'context': {'target_document_sha256': 'a' * 64, 'target_selected_facts': [],
                    'target_native_sku_inventory': {'status': 'verified_native_axes', 'all_option_states': []},
                    'eligible_target_body': {'blocks': [{'block_id': 'noise', 'support_text': 'noise'}, block]}}}
        table = deepcopy(full)
        table['context']['eligible_target_body']['blocks'] = [deepcopy(block)]
        invariant = {'attribute': '材質', 'attribute_quote': '材質', 'value': '綿', 'value_quote': '綿',
            'role': 'product_attribute', 'role_explanation': 'Material specification.',
            'subject_resolution': 'target_product', 'subject_scope': 'This product.',
            'target_identity_explanation': 'This product table.', 'quantifier_scope': 'All states.',
            'invariance_basis': 'positive_scope_wide_product_specification', 'family_audit': [],
            'semantic_explanation': 'The table supplies the material.', 'scope_evidence_quote': text,
            'positive_scope_explanation': 'Product-wide specification.',
            'product_scope_assessment': 'target_product_level', 'whole_attribute_assessed': True}
        decision = {'option_index': 0, 'whole_value_assessed': True, 'state': 'support',
            'scope_assessment': 'same_condition', 'semantic_relation': 'support',
            'scope_and_coordinate_explanation': 'Same material.', 'option_relation_explanation': 'Cotton.',
            'exclusivity_assessment': 'source_family', 'exclusivity_explanation': 'Single original option.',
            'grounding_route': 'body_invariant', 'target_fact_indices': [], 'body_invariant': invariant,
            'body_quote_refs': [{'body_container_index': 0, 'block_index': 0, 'quote': text, 'start': 0, 'end': len(text)}],
            'body_condition_bindings': [], 'direct_contradiction_explanation': ''}
        raw = {'protocol': EXP.VOLUME.INDEXED.PROTOCOL, 'pair_ref': 7, 'decisions': [decision]}
        mapping = {'body_index_maps': {'a' * 64: [{'local_index': 0, 'original_body_index': 1}]}}
        original_raw = deepcopy(raw)
        remapped = EXP.remap_table_response(raw, table, full, pair, 7, mapping)
        self.assertEqual(remapped['decisions'][0]['body_quote_refs'][0]['block_index'], 1)
        self.assertEqual(raw, original_raw)
        rendered = EXP.VOLUME.INDEXED.render_indexed_response(remapped, pair, full, 7)
        self.assertEqual(rendered['decisions'][0]['body_quote_refs'][0]['block_id'], 'table-b')
        hidden = deepcopy(raw)
        hidden['decisions'][0]['body_quote_refs'][0]['block_index'] = 1
        with self.assertRaises(ValueError):
            EXP.remap_table_response(hidden, table, full, pair, 7, mapping)
        fabricated = deepcopy(raw)
        fabricated['decisions'][0]['body_quote_refs'][0]['quote'] = 'noise'
        with self.assertRaises(ValueError):
            EXP.remap_table_response(fabricated, table, full, pair, 7, mapping)
        # The new normalized representation need not be an old text subset.
        # Its supplied quote must reach the dedicated raw-leaf validator.
        normalized = deepcopy(table)
        normalized['context']['eligible_target_body']['blocks'][0]['block_id'] = 'normalized-section'
        historical = deepcopy(full)
        historical['context']['eligible_target_body']['blocks'][1]['support_text'] = '材質綿'
        provenance = Mock(return_value={'source_citations_verified': True, 'citations': []})
        adapter = SimpleNamespace(validate_live_source_response=provenance)
        original_scope = Mock()
        bundle = SimpleNamespace(scope=SimpleNamespace(validate_source_response=original_scope), source_root=Path('/tmp/source'))
        with patch.object(EXP.importlib, 'import_module', return_value=adapter):
            result, evidence = EXP.validate_luna_response(raw, pair,
                {'table_payload': normalized, 'full_payload': historical}, 7,
                {'live_validation_context': {'source_view': 'description'}}, bundle,
                {'a' * 64: {'side': 'au'}}, 'table')
        self.assertEqual(result['decisions'][0]['body_quote_refs'][0]['block_id'], 'normalized-section')
        self.assertTrue(evidence['source_citations_verified'])
        self.assertIs(provenance.call_args.args[2], normalized)
        original_scope.assert_not_called()

    def test_immutable_draft_missing_termination_duplicate_or_foreign_refs_rejected(self):
        valid = b'{"pair_ref":7,"decisions":[]}\n'
        self.assertEqual(EXP.parse_assigned_draft(valid, [7])[0]['pair_ref'], 7)
        for raw, refs in ((valid[:-1], [7]), (valid + valid, [7]),
                          (valid, [8]), (b'{"pair_ref":true}\n', [1]),
                          (b'{"pair_ref":7,"pair_ref":8}\n', [8])):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    EXP.parse_assigned_draft(raw, refs)

    def test_packets_preserve_whole_large_families_and_small_group_limits(self):
        items = [{'pair_ref': i, 'payload': {'candidate_vector': [None] * count}}
                 for i, count in enumerate((3, 5, 2, 9, 1))]
        groups = EXP.packet_groups(items)
        self.assertEqual([[r['pair_ref'] for r in group] for group in groups], [[0, 1], [2], [3], [4]])
        self.assertEqual(sum(len(group) for group in groups), len(items))
        self.assertEqual(len(groups[2][0]['payload']['candidate_vector']), 9)

    def test_error_selection_prioritizes_complete_case_pair_cost(self):
        grade = {'variants': {'indexed-body-invariant-v3': {'differences': [
            {'case_id': 'b', 'difference': 'fn'}, {'case_id': 'a', 'difference': 'fn'},
            {'case_id': 'c', 'difference': 'fp'}]}}}
        selected = EXP.select_error_cases(grade, {'a': {'p'}, 'b': {'p', 'q'}}, 1)
        self.assertEqual(selected, ['a'])

    def test_small_model_never_manufactures_scope_or_citations(self):
        task = {'task_id': 't', 'request_id': 'r', 'case_id': 'c', 'au_row_key': 'row',
                'direction': 'rakuten_to_au', 'raw_condition_ref': {'axis_name': 'material', 'value': 'cotton'}}
        binding = {'task_binding': {'task_id': 't'}, 'pair_id': 'p', 'preparation_status': 'ready',
                   'source_selected_fact': {'value': 'cotton'}}
        subset = {'tasks': [task], 'bindings': [binding],
                  'proofs': [{'task_id': 't', 'status': 'unknown'}], 'receipts': [{'task_id': 't'}]}
        resolutions = EXP.small_model_resolutions(subset, {'p': ['support', 'conflict']},
                                                 {'p': {'option_values': ['cotton', 'silk']}})
        self.assertEqual(resolutions[0]['state'], 'support')
        self.assertFalse(resolutions[0]['source_citations_verified'])
        self.assertNotIn('scope_assessment', resolutions[0])
        self.assertNotIn('citations_verified_count', resolutions[0])
        missing = EXP.small_model_resolutions(subset, {}, {'p': {'option_values': ['cotton', 'silk']}})
        self.assertEqual(missing[0]['state'], 'unknown')
        wrong = deepcopy(subset)
        wrong['bindings'][0]['source_selected_fact']['value'] = 'silk'
        with self.assertRaises(ValueError):
            EXP.small_model_resolutions(wrong, {'p': ['support', 'conflict']}, {'p': {'option_values': ['cotton', 'silk']}})

    def test_small_model_complete_indices_and_state_with_score_metadata(self):
        pair = {'pair_id': 'p', 'option_values': ['one', 'two']}
        row = {'pair_ref': 7, 'decisions': [{'option_index': 0, 'state': 'support', 'score': 0.94},
                                        {'option_index': 1, 'state': 'explicit_conflict', 'chunks': 2}]}
        self.assertEqual(EXP.small_pair_states(row, pair, 7), ['support', 'conflict'])
        for malformed in ([row['decisions'][0]], [row['decisions'][1], row['decisions'][0]],
                          [{'option_index': True, 'state': 'support'}, row['decisions'][1]],
                          [{'option_index': 0, 'state': 'matched'}, row['decisions'][1]]):
            with self.assertRaises(ValueError):
                EXP.small_pair_states({**row, 'decisions': malformed}, pair, 7)

    def test_fixed_reference_reconstruction_retains_unknown_and_wrong_row(self):
        membership = [{'case_id': x, 'source_version': 'v2'} for x in ('a', 'b', 'c', 'd', 'e')]
        original = [{'case_id': 'a', 'decision': 'accept', 'top_row_key': 'row-a'},
                    {'case_id': 'b', 'decision': 'drop', 'top_row_key': None},
                    {'case_id': 'c', 'decision': 'drop', 'top_row_key': None},
                    {'case_id': 'd', 'decision': 'accept', 'top_row_key': 'wrong-d'},
                    {'case_id': 'e', 'decision': 'accept', 'top_row_key': 'unverified-e'}]
        actual = {'a': {'label': 'matched', 'matched_au_row_keys': ['row-a']},
                  'b': {'label': 'unmatched', 'matched_au_row_keys': []},
                  'c': {'label': 'matched', 'matched_au_row_keys': ['row-c']},
                  'd': {'label': 'matched', 'matched_au_row_keys': ['row-d']},
                  'e': {'label': 'unknown', 'matched_au_row_keys': None}}
        scored = EXP.VOLUME.SCORE_ORIGINAL.score_variant(membership, original, actual)
        cache = EXP.derive_reference_cache({'variants': {'indexed-body-invariant-v3': scored}}, original, membership)
        self.assertEqual(cache, actual)
        modified = deepcopy(original)
        modified[2].update(decision='accept', top_row_key='row-c')
        modified[0].update(decision='drop', top_row_key=None)
        change = EXP.paired_change(original, modified, cache)
        self.assertEqual(change['recovered'], 1)
        self.assertEqual(change['lost'], 1)
        self.assertEqual(sum(change.values()), 4)


if __name__ == '__main__':
    unittest.main()
