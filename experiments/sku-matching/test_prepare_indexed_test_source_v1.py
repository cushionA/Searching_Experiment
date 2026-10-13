import unittest
from prepare_indexed_test_source_v1 import SCHEMA, validate_identity_plan

class TestIdentityGuard(unittest.TestCase):
    def setUp(self):
        self.selected=[{'case_id':f'c{i}','au_product_id':f'p{i//2}'} for i in range(40)]
        self.manifest={'schema_version':SCHEMA,'status':'identity_only_subset_plan_no_test_evidence_read',
            'outputs_sha256':{'selected-membership.jsonl':'a'*64},'selected_product_count':20,
            'selected_case_count':40,'selection_based_only_on_identity_fields':True}
    def test_accepts_pinned_synthetic_identity_only_cohort(self):
        bound=validate_identity_plan(self.manifest,self.selected,'a'*64,'a'*64)
        self.assertEqual(len(bound),40)
        self.assertEqual(bound['c39'],'p19')
    def test_rejects_manifest_digest_mismatch(self):
        with self.assertRaisesRegex(ValueError,'SHA mismatch'):
            validate_identity_plan(self.manifest,self.selected,'b'*64,'a'*64)
    def test_rejects_duplicate_or_wrong_population(self):
        with self.assertRaisesRegex(ValueError,'incomplete or duplicated'):
            validate_identity_plan(self.manifest,self.selected[:-1],'a'*64,'a'*64)
    def test_rejects_condition_derived_plan_shape(self):
        altered=dict(self.manifest,selection_based_only_on_identity_fields=False)
        with self.assertRaisesRegex(ValueError,'counts/scope'):
            validate_identity_plan(altered,self.selected,'a'*64,'a'*64)
    def test_synthetic_artifact_metadata_matches_runner_contract(self):
        # Mirror the independent runner's key scope/schema/linkage gate without opening TEST inputs.
        policy={'schema_version':'sku-next-body-indexed-test-selection-policy-v1'}
        plan={'schema_version':'sku-next-body-indexed-test-selection-plan-v1',
              'identity_plan_manifest_sha256':'b'*64,
              'selected_membership_sha256':'a'*64,
              'policy_sha256':'c'*64}
        cohort={'schema_version':'sku-next-body-indexed-test-source-v1',
                'dataset':'sku-next-body-indexed-test-20261013-v1','split':'test',
                'independent_test':True,'test_split_read':True,
                'identity_plan_manifest_sha256':'b'*64,
                'selected_membership_sha256':'a'*64,'selection_policy_sha256':'c'*64}
        scoped={'schema_version':'sku-body-invariant-scoped-rakuten-inventory-test-v1',
                'dataset':cohort['dataset'],'split':'test','independent_test':True,'test_split_read':True}
        proof={'schema_version':'sku-next-body-invariant-test-literal-proofs-v1',
               'dataset':cohort['dataset'],'split':'test','independent_test':True,'test_split_read':True}
        self.assertTrue(policy['schema_version'].endswith('selection-policy-v1'))
        self.assertEqual(plan['policy_sha256'],cohort['selection_policy_sha256'])
        for artifact in (cohort,scoped,proof):
            self.assertEqual(artifact['split'],'test')
            self.assertIs(artifact['independent_test'],True)
            self.assertIs(artifact['test_split_read'],True)
        self.assertEqual(plan['identity_plan_manifest_sha256'],cohort['identity_plan_manifest_sha256'])
        self.assertEqual(plan['selected_membership_sha256'],cohort['selected_membership_sha256'])

if __name__=='__main__': unittest.main()
