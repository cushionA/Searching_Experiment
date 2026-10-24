---
license: apache-2.0
pipeline_tag: image-text-to-text
library_name: transformers
tags:
  - vision-language
  - object-detection
  - referring-expression-comprehension
  - object-counting
  - point-grounding
  - ocr
  - referring-expression-segmentation
  - image-matting
---

# WeVisOne-VL: Unified Visual Perception from Objects to Pixels

**WeVisOne-VL** (**We**Chat **Vis**ion All-in-**One**) is a unified vision-language model developed by WeChat Vision, Tencent Inc. Built upon Qwen3-VL, it unifies object-level perception (open-vocabulary object detection, referring localization, object counting, object pointing, and OCR) and pixel-level perception (referring segmentation and promptable alpha matting) through structured next-token prediction and a lightweight 9.5M-parameter pixel decoder.

## Quick Start

Install the inference package from [GitHub](https://github.com/Tencent/WeVisOne-VL), then:

```python
from PIL import Image
from wevisone_vl import WeVisOneVL

model = WeVisOneVL("tencent/WeVisOne-VL-2B", device="auto", dtype="bfloat16")
image = Image.open("example.jpg").convert("RGB")
result = model.predict(image, task="matting", query="person", temperature=0.0)
print(result.response, len(result.masks))
```

## Architecture & Released Revision

The model uses dedicated coordinate tokens (`<0>` ... `<1000>`) to represent normalized bounding boxes and points, returns `None` when a queried target is absent, and emits a special instance token (`<OBJ>`, token ID `152678`) after each predicted box for dense tasks.

The attached `mask_decoder.pt` (9,511,460 parameters) is a required core component that combines:
- A **Visual Feature Adapter** over multi-level Qwen3-VL pre-merging ViT features;
- A **Box-Guided Query Decoder** with 6 deformable cross-attention blocks initialized from the `<OBJ>` hidden state and Fourier-encoded box prior;
- An **Instance-Conditioned Dense Prediction Head** with `FullCanvasAlphaCascadeHead` (refining predictions across S/4, S/2, and S/1 to output full-resolution continuous alpha mattes at stride 1).

## Intended Use

The release is intended for research in visual grounding, referring localization, pointing, counting, OCR localization, referring segmentation, and image matting. It is not intended to make autonomous high-impact decisions or to identify individuals.

## Limitations

- Fine alpha boundaries remain sensitive to image resolution, severe occlusion, complex transparency, and prompt phrasing.
- For inference efficiency, the model directly generates structured outputs without explicit chain-of-thought (CoT) reasoning, and currently targets single-turn image perception (video grids are rejected by the pixel decoder).

## License

Unless otherwise noted, Tencent-authored code in this repository is released under the [Apache License 2.0](LICENSE).

Third-party components retain their original licenses and copyright notices. Please review the corresponding source files before use.
