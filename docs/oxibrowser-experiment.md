# OxiBrowser比較試験（2026-10-01）

ブランチ: `experiment/oxibrowser-20261001`。

OxiBrowser 0.25.0をLinuxでビルド・起動し、native HTTPクライアントとImpit 0.14.1を実サイトで比較した。CAストアを明示した後のHTTP結果には、今回の対象で改善を確認できなかった。AI向けのDOM観測はできたが、保存HTMLでの入力・クリックには成功フラグと実際の状態の不一致があった。実サイト上で検索・認証まで完了したとは扱わない。

このPRには報告とセレクタ記録を保存する。試験専用コード・設定・生の応答・ログは証拠ZIPにまとめ、共通インターフェイスやSkillは追加しない。

## 結果

HTTP欄はImpitのBFS/Lunaと、CA設定後のOxiのLuna armを比較する。OxiのBFSは導入時の接続失敗を含むため、同条件の成功率比較には使わない。

| 対象 | Impit | Oxi native HTTP（CA設定後） | Oxiで保存HTMLを観測・操作 |
|---|---|---|---|
| Google検索 `q=OxiBrowser` | robots判定で停止 | 同じ | 検索結果の取得・操作は未実施 |
| Amazonトップ | HTTP 200、本文約1.35MB | HTTP 200、本文約1.36MB | 本文の一部から80要素。検索欄・検索ボタンを観測、入力・クリックを試行 |
| Amazon検索 `k=USB-C` | HTTP 503 | HTTP 503 | 「セッションまたはクライアントはサポートしていません」。操作対象なし |
| ジョーシン | 301→`/top.html`、HTTP 200 | 同じ | 文字コードをCP932からUTF-8へ変換して1,139要素。検索欄を観測・入力 |
| Homesトップ・東京都賃貸一覧 | HTTP 200、439/390リンク | 同じ。トップの抽出本文ハッシュ一致 | 両ページとも`Page.navigate`が20秒でタイムアウト |
| Indeedトップ・Python求人検索 | HTTP 403 | HTTP 403 | `Security Check - Indeed.com`。認証画面の1リンク、求人操作は停止 |

Amazonトップの200応答には保存対象のContent-Typeがなく、既存実行器は`not_html`で本文抽出を停止した。HTML本文自体は保存済み。ジョーシンの応答は`text/html;charset=Windows-31J`で、既存のPython抽出器がこのcodec名を受け付けず`unknown_charset`になった。いずれもHTTPブロックと区別する。再生時の文字コード変換は別成果物に記録し、元の応答・失敗履歴を変更していない。

Googleの`/search`は保存したrobots.txtの`Disallow: /search`に該当する。この運用ではrobotsを維持するためSERPを取得できなかった。Googleトップも既存`RobotFileParser`が拒否したが、同parserは`Disallow: /?`のクエリ指定を`/`として扱う。トップの拒否はこの互換性問題を含み、GoogleのHTTP拒否やOxiの性能を示すものではない。

## AI操作

Lunaに観測済みの要素だけを渡し、fixture・Amazon・ジョーシンの操作を選ばせた。`OXI.getInteractiveElements`から得たselectorを次の観測で再照合し、新しいrefで`OXI.fillRef`/`OXI.clickRef`を呼ぶ。Amazon検索エラー、Indeed認証、Homesの観測不能には操作を送っていない。

fixtureでは`fillRef`が成功を返し、`Runtime.evaluate`でも入力値`OxiBrowser`を確認した。`clickRef`は成功を返したが、onclickで更新されるはずの結果は`No results yet`のままだった。このdata URL再生経路では、APIの成功フラグだけで操作の効果を認定できない。ライブの通常navigationでも同じ不具合が起こるとは未確認。

Amazon・ジョーシンでも`fillRef`は`filled: true`を返したが、各操作直後の`Runtime.evaluate`では対象inputの値が空だった。Amazonの検索ボタンも`clicked: true`を返したが、検索結果への遷移は確認できなかった。ImpitはHTTPクライアントとして比較し、AIの入力・クリックはOxiだけで行った。

保存HTMLはdata URLで再生するため、元のorigin・Cookie・外部スクリプト・画像・検索応答を再現していない。`Network.emulateNetworkConditions(offline=true)`に加え、閉じたループバックproxyを指定する。外部通信・課金API・ログイン・購入は発生させない。したがって入力値や要素認識は確認できるが、実サイトでのフォーム送信成功、CAPTCHA解決、ブロック突破の評価には使えない。

OxiのCDP入力メッセージは1MiB上限。Amazon全文のdata URLはこれを超えて接続が切れたため、本文の先頭部分を別の再生試験で使用した。`replay_truncated`、元本文SHA256、再生本文SHA256を記録する。80要素はページ全体の要素数ではない。

## 操作に使ったセレクタ

2026-10-01に取得したHTMLでの記録。CSS selectorを`OXI.getInteractiveElements`で再照合し、その時点のrefを`OXI.fillRef`/`OXI.clickRef`へ渡した。`e91`などのrefは観測ごとに変わるため、再利用する識別子にはしない。

| サイト・ページ | CSS selector | 観測した要素 | 実行した操作 | 直後に確認できた結果 |
|---|---|---|---|---|
| Amazonトップ `https://www.amazon.co.jp/` | `#twotabsearchtextbox` | `input[type=text]`、searchbox、名前「Amazon.co.jpを検索」 | `fillRef`で`USB-C` | `filled: true`。inputの値は空 |
| 同上 | `#nav-search-submit-button` | `input[type=submit]`、button、名前「検索」 | `clickRef` | `clicked: true`。結果遷移は確認できず |
| ジョーシン `https://joshinweb.jp/top.html` | `#suggest_input` | input、textbox | `fillRef`で`USB-C` | `filled: true`。inputの値は空 |
| Google検索 | 使用なし | robots判定で停止 | 操作なし | SERP未取得 |
| Amazon検索エラー | 使用なし | HTTP 503のエラー本文 | 操作なし | 検索結果を取得できず |
| Homesトップ・東京都賃貸一覧 | 使用なし | HTML再生のnavigateがタイムアウト | 操作なし | 操作用要素を確認できず |
| Indeedトップ・求人検索 | 使用なし | HTTP 403、追加検証画面 | 操作なし | 求人操作を停止 |
| ローカルfixture | `#query` | input、textbox、名前「Search query」 | `fillRef`で`OxiBrowser` | 入力値`OxiBrowser`を確認 |
| 同上 | `#search` | button、名前「Search」 | `clickRef` | `clicked: true`。結果本文は`No results yet`のまま |

機械で読める記録は[oxibrowser-selectors-20261001.json](oxibrowser-selectors-20261001.json)。操作の値・API応答・直後のDOM観測・元本文のSHA256を含む。未操作サイトのセレクタは推測して補完しない。全サイトについてライブ画面での有効性は未検証で、Amazonは本文の部分再生、ジョーシンは文字コード変換後の観測である。

次回は現在のURLと要素の名前・typeを照合してから操作し、APIの成功フラグに加えて入力値や遷移先・結果本文を確認する。HTTP拒否や認証画面を検出した場合は、その画面を通常の検索フォームとして扱わない。

## 接続と計測条件

- upstream: [project-oxi/oxibrowser](https://github.com/project-oxi/oxibrowser)、revision `0aaa7d04a565d8840f889cfb57deb44b424c6cc2`。
- releaseのgeneric `oxibrowser`はmagic `cafebabe`のmacOS universal binary。Linuxとして起動していない。Linux Actions成果物の取得経路は環境proxyのCONNECT 403で利用できず、ソースをビルドした。
- Rust 1.96.0、Python 3.12.14、CMake 4.1.0。debug buildなので、READMEのrelease時の起動速度・44MBという公称値は検証していない。今回のdebug binaryは約179MiB、HTTP helperは約29MiB。
- `scripts/prepare_oxibrowser.py`がcoreへ`lab_follow_redirects`（既定true）を追加する。helperではfalseにし、自動redirectを止め、既存Fetcherが各hopを計上する。helperだけにシステムCAストアを明示する。証明書検証を維持し、例外詳細を記録する。helperは同じOxi `HttpClient`/wreq/Chrome149 emulationを使う。
- HTTPは各GETごとに新規helperを起動し、Cookieを持ち越さない。nativeクライアントを使うが、標準Oxi CLIのCookieセッションやJSリソース取得をそのまま測った試験ではない。
- 全実サイト通信は環境proxy経由。直結時は既存の検査済みpublic IP固定CONNECTトンネルを再利用する。proxyのTLS終端・送信元IPやJA4を実測していないため、fingerprintだけを原因とする性能比較はできない。
- 2方式×2arm、各arm最大9ページ/9試行/24HTTP/24MB、応答上限2MB、depth 0、2秒間隔、15秒timeout。9固定URLの取得で、Lunaのリンク探索品質や統計的な優位性を測っていない。
- 全体50 HTTP、22,434,504 charged bytes。保存できた実応答本文は6,434,504 bytes、導入時の8失敗について16,000,000 bytesの予約を保持した。予算のリセットや新runでの取り直しはしていない。
- OxiのCAストア明示後、同じrunの残予算でGoogle robotsをHTTP 200取得し、残りのBFSとLunaを続行した。修正前の例外は`Connect`までしか保存しておらず、当時の失敗原因をサイト側の拒否と断定しない。
- 調査・計画・総括・操作選択はモデル指定native Lunaへ委譲。実モデルID・tokens・費用の独立検証はできず`model_runtime_verified=false`。

CDP Fetch interceptionは全通信を覆わない。トップDocumentと一部JS取得だけで、stylesheet、iframe、子スクリプト、subresourceの直接経路が残る。Fulfillはdata URLに置換され、originも変わる。v0.25.0のdata URL読み込みはbase64をデコードせず文字列として表示したので、再生にはpercent encodingを使用した。全通信を既存のrobots・予算・証拠保存へ通すライブブラウザ経路は、今回の実装には接続していない。

## 再実行

再実行用コードはPRの実装には含めず、証拠ZIP内の`harness/cloud-package.zip`にまとめた。試験時のソース一式を別ディレクトリに展開して使う。設定2件は証拠ZIPの`harness/experiments/`にも保存する。

通常のLinuxではRust 1.96以上、C/C++ toolchain、CMake、libclangとそのresource headers、fontconfig/freetype/expatの開発用パッケージを用意する。展開したソースでproxyとCA環境変数を維持して次を実行する。

```bash
bash scripts/setup_oxibrowser.sh
python3 -B -m unittest discover -s tests -p 'test_lab*.py'
.deps/oxibrowser-venv/bin/python -B -m unittest discover -s tests -p 'test_impit.py'
```

このCloudではOS全体へパッケージを追加せず、Rust/CMakeとDebian開発用ファイルを`.deps`へ配置した。ビルドには`.deps/cargo/bin`とvenvのbinをPATHへ加え、`RUSTUP_HOME`/`CARGO_HOME`、`LIBCLANG_PATH`、clang resource headersの`BINDGEN_EXTRA_CLANG_ARGS`、展開先の`PKG_CONFIG_PATH`/`PKG_CONFIG_SYSROOT_DIR`、`PKG_CONFIG_ALL_DYNAMIC=1`を指定した。環境を再作成する場合は通常の開発用パッケージ導入か同等の配置が必要。

ライブの新試験には対象・上限を確認して別のrun名を使う。今回のrun名を上書きしない。

```bash
.deps/oxibrowser-venv/bin/python -B -m jse.lab init \
  --config experiments/oxi-oxibrowser-20261001.json --run .lab-output/your-new-run
.deps/oxibrowser-venv/bin/python -B -m jse.lab run --run .lab-output/your-new-run
```

以後は`docs/cloud-lab.md`のLuna回答・判断・運転手順を使う。`transport=oxibrowser`はnative HTTP取得のみ。helperは既定で`.deps/oxibrowser-src/target/debug/oxi-lab-http`、別配置は`OXI_HTTP_BIN`で指定する。

保存HTMLの再生は追加の実サイト取得を行わない。

```bash
.deps/oxibrowser-venv/bin/python -B scripts/oxi_replay.py \
  --run .lab-output/oxi-impit-20261001 --output .lab-output/your-observation.json
.deps/oxibrowser-venv/bin/python -B scripts/oxi_replay.py \
  --run .lab-output/oxi-oxibrowser-20261001 --arm luna \
  --output .lab-output/your-native-observation.json
```

観測にあるselectorを選んだ`{"fixture":[{"op":"fill","selector":"#query","value":"OxiBrowser"}]}`形式のJSONを`--actions`で渡せる。既存出力を上書きせず、各ページ3操作まで。モデルの任意JSは実行しない。

## 保存物・次の活用

両runのverify/exportは成功。結果判断待ちで停止し、次の取得を自動では開始しない。

- `.lab-output/oxi-impit-20261001-checkpoint.zip`
- `.lab-output/oxi-oxibrowser-20261001-checkpoint.zip`
- `.lab-output/oxi-replay-bounded-observe.json`、`oxi-native-replay-observe.json`、`oxi-replay-actions-immediate.json`
- `.lab-output/oxi-browser-action-request.json`、`oxi-browser-action-answer.json`
- `.lab-output/oxi-evidence-20261001.zip`: checkpoint・再生結果・ログ・ソース差分・ハッシュmanifestの持ち出し用。バイナリや資格情報は含めない。

既存labの46テストとImpitの7テストが成功した。生のサイト本文をGitへ載せず、本レポート・セレクタ記録・READMEへのリンクをブランチへ保存する。Cloudキャッシュを永続保管庫とは扱わず、継続時には上記ZIPを取り出す。

今回から有望なのは、静的HTMLのテキスト・構造抽出や、取得済みページをAIへ小さい要素リストとして渡す用途。Impitとの差はHTTP拒否の改善ではなくDOM/Markdown/refsという操作面にある。ライブフォーム操作に使う前に、全リソースの台帳接続、origin保持、クリック後のURL/DOM変化の検証が必要。Google SERP、認証・Cookieを要する検索、JSの多いページは、この測定だけではOxiへの置き換えを判断できない。
