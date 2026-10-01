# セレクタ候補とチャレンジ画面

初回実サイト調査では操作は未実施。保存済みDOM内の一致確認まで実施した。共通フレームワークの操作オプションは別途fixtureで検証する。ボタンだけで突破できるCAPTCHAは今回確認できていない。IndeedのReturn homeは通常の遷移リンクで、CAPTCHA突破ボタンではない。

| サイト | 候補 | DOM一致数 |
|---|---|---|
| amazon | `#twotabsearchtextbox` | 1 |
| joshin | `#suggest_input` | 1 |
| homes | `button[data-open-menu][aria-controls="Menu"]` | 1 |
| indeed | `#returnHome` | 1 |

## チャレンジ・拒否画面

### wreq-js / amazon (202)

[保存HTML](/workspace/Searching_Experiment/lab-runs/bot-diagnostics-evidence/wreq-js-amazon-challenge.html)

実画面の画像なし。保存HTMLを保持。

### wreq-js / indeed (403)

[保存HTML](/workspace/Searching_Experiment/lab-runs/bot-diagnostics-evidence/wreq-js-indeed-challenge.html)

実画面の画像なし。保存HTMLを保持。

### impit / amazon (202)

[保存HTML](/workspace/Searching_Experiment/lab-runs/bot-diagnostics-evidence/impit-amazon-challenge.html)

実画面の画像なし。保存HTMLを保持。

### impit / joshin (403)

[保存HTML](/workspace/Searching_Experiment/lab-runs/bot-diagnostics-evidence/impit-joshin-challenge.html)

実画面の画像なし。保存HTMLを保持。

### impit / indeed (403)

[保存HTML](/workspace/Searching_Experiment/lab-runs/bot-diagnostics-evidence/impit-indeed-challenge.html)

実画面の画像なし。保存HTMLを保持。

### playwright-baseline / amazon (202)

[保存HTML](/workspace/Searching_Experiment/lab-runs/bot-diagnostics-evidence/playwright-baseline-amazon-challenge.html)

![保存画面](/workspace/Searching_Experiment/lab-runs/bot-diagnostics-evidence/playwright-baseline-amazon-challenge.png)

### playwright-baseline / indeed (403)

[保存HTML](/workspace/Searching_Experiment/lab-runs/bot-diagnostics-evidence/playwright-baseline-indeed-challenge.html)

![保存画面](/workspace/Searching_Experiment/lab-runs/bot-diagnostics-evidence/playwright-baseline-indeed-challenge.png)

### patchright / amazon (202)

[保存HTML](/workspace/Searching_Experiment/lab-runs/bot-diagnostics-evidence/patchright-amazon-challenge.html)

![保存画面](/workspace/Searching_Experiment/lab-runs/bot-diagnostics-evidence/patchright-amazon-challenge.png)

### patchright / indeed (403)

[保存HTML](/workspace/Searching_Experiment/lab-runs/bot-diagnostics-evidence/patchright-indeed-challenge.html)

![保存画面](/workspace/Searching_Experiment/lab-runs/bot-diagnostics-evidence/patchright-indeed-challenge.png)

### rebrowser-lightpanda / indeed (403)

[保存HTML](/workspace/Searching_Experiment/lab-runs/bot-diagnostics-evidence/rebrowser-lightpanda-indeed-challenge.html)

実画面の画像なし。保存HTMLを保持。
