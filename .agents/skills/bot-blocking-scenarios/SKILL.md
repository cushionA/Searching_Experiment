---
name: bot-blocking-scenarios
description: 共通フレームワークへURL・セレクタを注入するサイト別ブロッキング検証シナリオを作成・更新する。
---

# ブロッキング検証シナリオ

このリポジトリで検知器・初回応答・サイト内ナビゲーションの比較を再実行、または対象サイトのシナリオを追加するときに使う。

## 共通実装を再利用する

`framework.mjs`がセッション・再開のメインループ、`manifest.mjs`がURL・オプションの検査、`scenario.mjs`が役割と段階の実行、`adapters.mjs`が各ツールのnative fetch/gotoである。`runner.mjs`の通信は`evidence.mjs`の回数・bytes台帳を通し、`runtime.mjs`の共通設定を使う。サイト別にこれらを書き直さない。

1. `sites.json`を基に、当該run専用の設定を作る。home、targets、origins、params、selectorsを注入する。URLテンプレートの値はpercent encodeされる。
2. サイト固有の分岐が必要なときだけ、小さい`.mjs`に`export const roles = { homepage, target, returnHome, selectorProbe, recoverSimpleChallenge, extensionProbe }`の必要な役割だけ実装する。メソッドは`({ adapter, site, links, selectors, params, url })`を受け取り、共通adapterの役割メソッドを使う。未指定の役割は標準実装になる。
3. 共通pipelineの証拠保存を使い、通常のbrowser profileで通信・リダイレクト・応答本文・DOM・スクリーンショット・失敗を記録し、取得失敗、サービスchallenge、環境blockを区別する。grounding探索用の予算、robots gate、同一origin/GET/resource block、人工delayはこのbot-diagnosticsシナリオに適用しない。実際の環境sandbox、proxy、TLS制御は別の境界として保持し、無効化しない。
4. 同じ方式×サイト×比較条件内の段階は同じセッションを維持し、比較条件の間はセッションを分ける。各方式に無い機能は`unsupported_capability`で記録する。HTTPクライアントの取得をDOM操作の成功として扱わない。
5. `plan`でURL展開と範囲を確認し、fixtureとオフライン検証を通してから、ユーザーが指定した対象・上限の範囲を実行する。対象を広げる場合は実際のユーザー指示に従う。Googleは最後の任意段階として明示的に選択する。

## 操作オプション

selectorProbe、recoverSimpleChallenge、拡張機能・操作速度の比較は個別フラグまたは`--all-options`で選択する。Skillは毎回この共通実装を呼ぶ。

`site.operations`へ最大3つのfill/click/hoverを注入する。fillは入力後の値、click/hoverは必須の`expect`（text/attribute/present/url/value）で実際の変化を確認する。一意のセレクタ、入力要素のtag/type、現在のURLを照合する。元から真の成功条件やAPI成功フラグだけでは効果を認定しない。

ボタンが存在するだけでは、クリックで突破できるとは判断しない。ログイン、同意、Return home、通常のナビゲーションをCAPTCHA突破ボタンと混同しない。現在の`selector-candidates.json`と`lab-runs/bot-diagnostics-evidence/challenge-index.json`を参照する。

回復は`recovery: { kind: "simple_button", selector, success }`を設定した場合だけ実行する。前後の通信・本文・DOM・スクリーンショットを通常のbrowser profileで記録し、同じセッションでhomeへ戻って元のtargetへ1回だけ再訪する。人工的な待機は挿入しない。結果では取得失敗、サービスchallenge、環境によるblockを区別する。必要以上に再試行しない。画像パズルは通常表示で観測できた範囲を記録し、証拠が足りなければ未評価とする。表示・観測できるものを一律`unsupported_capability`としない。現在の実サイトには観測済みの単純な突破ボタンがないためrecoveryはnull。ボタンがあるだけでは突破と認定せず、成功条件でDOMを確認した場合もHTTPの元statusを200へ書き換えない。

拡張は通信を発生させない観測用MV3を同梱する。custom extensionはcontent-scriptのみ・権限なし・背景処理なしの小さいローカル実装に限り、読み込みを検証するprobeを注入する。Chromeの管理ポリシー拒否は`environment_policy_blocked`としてサイト拒否と区別する。実験専用Chromeを使う比較では全Chromium条件の実行ファイルをそろえる。人間に近い操作タイミングや拡張の存在を検知耐性の改善とは仮定しない。

## 解釈と保存

Rebrowser/BotDの公開ルールと、実サイトのWAFスコアを区別する。`providers.mjs`の製品・widgetの手掛かりは原因確定や採点式ではない。プロキシ、リソース制限、検知器の未対応API、robots拒否、通信エラーを結果へ明記する。

設定・実装のハッシュ、元HTML、DOM、native screenshot、項目別判定、台帳、再開履歴を保存する。存在しないセレクタを観測済みと記載しない。`--resume`は同じ設定・残予算を使い、Cookieを含むセッションは再起動されることを明記する。終了時にverifyし、`python3 -B experiments/bot-diagnostics/export.py --run RUN --output CHECKPOINT.zip`でコード・状態・証拠を取り出せる状態にする。

参照: `experiments/bot-diagnostics/README.md`、`docs/bot-diagnostics-2026-10-01.md`。
