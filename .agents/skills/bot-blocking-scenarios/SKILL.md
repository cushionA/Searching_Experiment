---
name: bot-blocking-scenarios
description: 共通フレームワークへURL・セレクタを注入するサイト別ブロッキング検証シナリオを作成・更新する。
---

# ブロッキング検証シナリオ

このリポジトリで検知器・初回応答・サイト内ナビゲーションの比較を再実行、または対象サイトのシナリオを追加するときに使う。

## 共通実装を再利用する

`experiments/bot-diagnostics/framework.mjs` がメインループ、`adapters.mjs` が各ツールのセッションとnative fetch/goto、`runner.mjs` がrobots・回数・bytes・証拠の実行境界である。サイト別にこれらを書き直さない。

1. `sites.json`を基に、当該run専用の設定を作る。home、targets、origins、params、selectorsを注入する。URLテンプレートの値はpercent encodeされる。
2. サイト固有の分岐が必要なときだけ、小さい`.mjs`に`export const roles = { homepage, target, returnHome }`を実装する。メソッドは`({ adapter, links, selectors, params, url })`を受け取り、`adapter.homepage` / `adapter.followLink` / `adapter.returnHome`を使う。
3. 手作業のHTTP・裸のブラウザAPIで台帳を迂回しない。共通adapterへ機能を追加する場合は、ネットワーク要求・リダイレクト・失敗も既存の予算へ計上する。段階ごとに予算を作り直さない。
4. 同じ方式×サイト内の段階は同じセッションを維持する。各方式に無い機能は`unsupported_capability`で記録する。HTTPクライアントの取得をDOM操作の成功として扱わない。
5. `plan`でURL展開と範囲を確認し、fixtureとオフライン検証を通してから、ユーザーが指定した対象・上限の範囲を実行する。対象を広げる場合は実際のユーザー指示に従う。Googleは最後の任意段階として明示的に選択する。

## 現在保留している役割

ユーザーの「セレクタ操作セクションに関しては少し待って」という指定により、selectorProbe、recoverSimpleChallenge、拡張機能導入・操作は実装・実行を保留する。ユーザーがそのセクションを再開するまで、候補の保存とDOM内の一致確認だけを行う。ユーザーの新しい指示がこの保留を更新した場合、その指示を優先する。

ボタンが存在するだけでは、クリックで突破できるとは判断しない。ログイン、同意、Return home、通常のナビゲーションをCAPTCHA突破ボタンと混同しない。現在の`selector-candidates.json`と`lab-runs/bot-diagnostics-evidence/challenge-index.json`を参照する。

操作セクションが再開された後の役割は、簡単な操作の前後の証拠を保存し、通常本文の取得で突破を確認し、同じセッションでhomeへ戻り、指定秒数待機して元のtargetへ1回再訪する。試行上限を共有し、未対応・画像パズル・失敗を無限に再試行しない。

## 解釈と保存

Rebrowser/BotDの公開ルールと、実サイトのWAFスコアを区別する。`providers.mjs`の製品・widgetの手掛かりは原因確定や採点式ではない。プロキシ、リソース制限、検知器の未対応API、robots拒否、通信エラーを結果へ明記する。

設定・実装のハッシュ、元HTML、DOM、native screenshot、項目別判定、台帳、再開履歴を保存する。存在しないセレクタを観測済みと記載しない。継続は同じrunの残予算を使い、終了時にverifyしてZIPを取り出せる状態にする。

参照: `experiments/bot-diagnostics/README.md`、`docs/bot-diagnostics-2026-10-01.md`。
