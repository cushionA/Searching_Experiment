"""Synthetic regression for source-coordinate request-vector partitioning."""
import importlib.util
from pathlib import Path
import unittest

SCRIPT=Path(__file__).with_name("prepare_next_body_invariant_dev_source_v1.py")
spec=importlib.util.spec_from_file_location("body_invariant_source",SCRIPT)
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)

def derive(original, task, family):
    module.validate_native_family(family)
    source=family["family_source"]
    locator=family.get("axis_key") or family["family_source"]["locator"].get("path") or family["family_source"]["locator"].get("json_path")
    coordkey={"direction":task["direction"],"axis_key":task["axis_key"],"source_axis_key_or_path":locator,
      "raw_file":source["raw_file"],"raw_sha256":source["sha256"],"ordered_native_family":family["family_values"]}
    sem={k:original[k] for k in ("direction","axis_name","selected_value","target_document_id","target_document_sha256","target_selected_axes","target_selected_axes_text")}
    sem["option_values"]=list(family["family_values"])
    return "generic-description-derived:"+module.digest({"source_coordinate_family":coordkey,"request_semantics":sem}),sem

class RequestVectorRepair(unittest.TestCase):
    def test_same_caption_distinct_coordinates_keep_full_ordered_vectors(self):
        base={"direction":"rakuten_to_au","axis_name":"色","selected_value":"赤","target_document_id":"d","target_document_sha256":"s","target_selected_axes":[],"target_selected_axes_text":""}
        a={"family_source":{"raw_file":"a.html","sha256":"a"*64,"locator":{"path":"variantSelectors[0].label"}},"axis_key":"color-a","family_values":["赤","青","緑"]}
        b={"family_source":{"raw_file":"b.html","sha256":"b"*64,"locator":{"path":"variantSelectors[2].label"}},"axis_key":"color-b","family_values":["赤","白"]}
        ia,ra=derive(base,{"direction":"rakuten_to_au","axis_key":"color-a"},a)
        ib,rb=derive(base,{"direction":"rakuten_to_au","axis_key":"color-b"},b)
        self.assertNotEqual(ia,ib)
        self.assertEqual(ra["option_values"],["赤","青","緑"])
        self.assertEqual(rb["option_values"],["赤","白"])
        self.assertNotEqual(ra["option_values"],list(dict.fromkeys(ra["option_values"]+rb["option_values"])))

    def test_missing_or_wrong_source_receipt_fails_closed(self):
        for family in ({}, {"family_source":{"raw_file":"x","sha256":"wrong","locator":{}},"axis_key":"x","family_values":[]},
                       {"family_source":{"raw_file":"x","sha256":"x","locator":{}},"family_values":["x"]}):
            with self.assertRaises((KeyError,TypeError,ValueError)):
                derive({}, {"direction":"au_to_rakuten","axis_key":"k"}, family)

if __name__=="__main__": unittest.main()
