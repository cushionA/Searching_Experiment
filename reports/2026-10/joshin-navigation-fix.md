# ジョーシンの4play遷移修正（2026-10-07）

4playの新規タブを目的URLへ直接開く処理を、同じコンテナの空タブが完了してからページのMAIN worldで通常の `location.assign` を実行する処理へ変更した。HTTP bridge、native controller、Camoufox + 4playが共通のhelperを使う。ヘッダーの書き換え、マウス・ホイール・キーボード操作は行っていない。

空タブの完了通知を目的ページの完了として扱わない。対象タブ・コンテナ・HTTP main-frame応答・完了URLを照合し、リダイレクト先を待つ。403本文とstatusは保存し、通信失敗へ置き換えない。空タブ待機・注入に失敗した場合は新しいタブを閉じ、native側は以前のタブへ戻る。

## 実測

最新mainは `3a197da67cac7103f659c3c00833b1142a64c8df`。元の調査は [joshin-blocking-20261007](../../benchmarks/joshin-blocking-20261007/) に残した。新しい比較と実装後の証拠は [joshin-navigation-20261007](../../benchmarks/joshin-navigation-20261007/) に保存した。

| 条件 | 最初のURL | トップ200 / 403 / proxy503 | 内部ページ200 / 403 / proxy503 |
|---|---|---:|---:|
| 元の4play、待機条件2種 | `/` | 0 / 4 / 0 | 未実行 |
| 空タブ→tabs.update、アセンブル版 | `/` | 0 / 2 / 0 | 未実行 |
| 空タブ→MAIN location.assign、拡張内での試作 | `/` | 2 / 0 / 0 | 4 / 0 / 0 |
| 空タブ→ISOLATED location.assign、拡張内での試作 | `/` | 2 / 0 / 0 | 2 / 1 / 0 |
| 正式API、通常4play、ISOLATEDからの遷移 | `/` | 4 / 0 / 0 | 8 / 0 / 0 |
| 最終helper、通常4play | `/` | 3 / 0 / 1 | 4 / 0 / 1 |
| 最終helper、アセンブル版 | `/` | 0 / 2 / 0 | 未実行 |
| 同時期のCamoufox対照、保存fingerprint | `/` | 0 / 4 / 0 | 未実行 |
| 同時期のCamoufox対照、新規fingerprint | `/` | 0 / 4 / 0 | 未実行 |
| 最終helper、アセンブル版 | `/top.html` | 1 / 0 / 1 | 2 / 0 / 0 |
| 同時期のCamoufox対照 | `/top.html` | 2 / 0 / 2 | 4 / 0 / 0 |
| 最後のアセンブル版再確認 | `/top.html` | 0 / 0 / 2 | 未実行 |

内部ページは既存manifestの `/tobuy.html` と `/javacookie.html`。表の内部ページは同じCookieコンテナを維持した新規タブへのgotoであり、実際の同一タブのリンククリックは別のloopback fixtureで検証した。各試行で本文・DOM・検索入力 `#suggest_input` を確認し、403から200へのstatus変更はしていない。

元の拡張から新規タブを直接開くと、freshな文脈でも `Sec-Fetch-Site: same-origin` が記録された。空タブからのページ内遷移ではブラウザが `cross-site` を生成した。先行のHTTP比較も、同じクライアントで `same-origin` の2条件が403、`none` の2条件が200だった。待機延長より遷移方式が有力な要因だが、Akamaiの内部ルールや点数は取得していない。後半はCamoufox自身も `/` で403になっており、この拒否を4play固有とは断定できない。`/` と直接の `/top.html` は別条件として残している。

503の本文は `upstream connect error ... Invalid argument|remote address:envoy://cloudflare_https_tunnel/`。Cloudプロキシ側の接続失敗を観測しており、Akamaiの403と混同しない。最終の再確認はこの503で止まった。Cloudのnetwork policyはunrestricted/enforced、observations_current=trueのままで、外部通信は継承proxyとCAを使った。出口IPの一致は確認していない。

## 検証と再現

Firefox ESRとCamoufoxの実ブラウザfixtureで、リダイレクト完了、A→B→Cの実リンク移動、同一タブ・コンテナ、CookieとReferer、GET/POSTの403本文・method保存が通った。関連Nodeテスト31件、必須labテスト151件（skip15）が通過した。元の署名済み拡張1.10のXPIは変更していない。native起動時のコピーには既存の接続設定patchだけを適用する。

```sh
python3 -B experiments/fourget-selfhost/start.py
node --test experiments/bot-diagnostics/fourplay-tab-navigation.test.mjs experiments/bot-diagnostics/fourplay-native-runtime.test.mjs experiments/bot-diagnostics/fourplay-bridge-gate.test.mjs experiments/bot-diagnostics/camoufox-fourplay-runtime.test.mjs experiments/bot-diagnostics/camoufox-runtime.test.mjs
python3 -B -m unittest discover -s tests -p 'test_lab*.py'
node experiments/bot-diagnostics/joshin-wait-diagnostics.mjs NEW_RUN --4play
```

Camoufox binary・元の4play拡張・web-ext・DISPLAYが配備済みなら、`fourplay-navigation-fixture.mjs NEW_RUN --native|--hybrid` がローカルfixtureを実行する。アセンブル版の保存fingerprint比較は `joshin-wait-diagnostics.mjs NEW_RUN --hybrid CAMOUFOX_SEED_RUN https://joshinweb.jp/top.html`。各出力先は新しいディレクトリを指定する。元のサイトmanifestは変更していない。

Checkpointには生の台帳、応答・DOM・ソースblob、fingerprint、条件、検証結果、最終実装と起動レシピを含める。fixtureのWebSocket接続ログから一時認証トークンを伏せたコピーを共有し、元のローカル観測は保持した。以後のfixture記録でもこの接続パスを伏せる。開発中のソースsnapshotと、実際にインストールしたbridgeの版は分ける。初期の正式API測定はISOLATED遷移のimage、最終imageはMAIN helperであり、実装と測定時刻の異なる結果を合算した通過率として扱わない。
