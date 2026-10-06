Fine-tuned CAPTCHA CRNN / ONNX Runtime CPU

Selected checkpoint: epoch 37, validation selection only.
Model: captcha-crnn-finetuned.onnx (14,287,573 bytes)
SHA-256: 63f750d945030a32f43c088e869db436e0dda1c321651e3f849f9df0c6b6733d
Checkpoint SHA-256: cce95d7cd320d332fecb606b39ccdcf269eb9794093bea7e2c0a4e4b30c11691

Runtime installation (Python 3.12):
  python -m pip install -r requirements-runtime.txt

Read a whole CAPTCHA image:
  python recognize_finetuned_crnn_onnx.py --model captcha-crnn-finetuned.onnx --model-sha256 63f750d945030a32f43c088e869db436e0dda1c321651e3f849f9df0c6b6733d --image captcha.png

Python integration:
  from pathlib import Path
  from PIL import Image
  from recognize_finetuned_crnn_onnx import CRNNOnnxOCR
  ocr = CRNNOnnxOCR(Path('captcha-crnn-finetuned.onnx'), '63f750d945030a32f43c088e869db436e0dda1c321651e3f849f9df0c6b6733d')
  with Image.open('captcha.png') as image:
      answer = ocr.predict(image)

Use this adapter when replacing common_old calls. It performs the required whole-
image grayscale conversion, PIL bicubic resize to 150x40, float32 division by255
and greedy CTC decoding. CPU intra/inter threads are2/1. Runtime needs only ONNX
Runtime, NumPy and Pillow; PyTorch/training configuration are export-audit inputs.

Graph contract:
  Opset17 / ONNX IR8 / dynamic batch / all weights inline / no quantization.
  pixel_values: float32 [batch,1,40,150], values in [0,1]
  logits: float32 [batch,37,63]
  Blank=0; remaining classes: abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789
  Decode: argmax, collapse adjacent equal tokens, remove blank. Blank resets
  adjacency, so a/blank/a decodes as aa. No case conversion or punctuation filter.

Verification on the unchanged frozen internal test fold:
  614 images = 214 alphanumeric +400 digits.
  Alphanumeric exact201/214 (93.9252%); digits exact366/400 (91.5%).
  ONNX CPU batch1 andbatch32 both reproduce all614 PyTorch/GPU decoded strings.
  All prediction JSONL SHA-256:
    2ec47a8a0acd50f4281a7d2e2f39bbbbb6b0c14202306c7e2edd111ae3c1c122
  ONNX/PyTorch preprocessing is bitwise equal. Floating-point logits differ
  (batch32 maxabs0.00145721, meanabs1.09670e-5); token argmax differences=0.
  See verification.json and numerical-audit.json for counts and source hashes.

These public images were already used in earlier model-selection comparisons.
The internal split excludes training images, but it is not a freshly collected
independent final test set. Pretrained-data overlap is unknown. No live-site pass
rate or cross-runtime speed advantage is claimed.
