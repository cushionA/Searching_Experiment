# 無料の検索結果取得を試す

2026年10月2日（日本時間）、無料・APIキーなしで検索結果を取得できる入口を実地確認した。検索API契約・登録・有料呼出しは行っていない。取得したのは公開検索ページのHTMLで、既存の実行器のrobots、HTTP、待機、保存、予算管理を使った。

## 保存時点の方針

ユーザーの「4getはブロッキング対策で抜ける可能性もある」「これはかなり有力な候補として残す」という指示を受け、**4getをかなり有力な候補として優先保持する**。今回のブロックだけを理由に候補から落とさない。Googleを選べる画面、日本語の検索語、日本・日本語の選択肢を確認できたことが候補を残す根拠で、Google由来の実際の検索結果と、ブロック対策の有効性はまだ確認していない。

4get.caのIQテスト画面と、4get.chの環境proxyによるCONNECT 403は異なる失敗として保存する。再開時はこの記録から取得経路を検討する。今回の保存作業では追加通信やブロック対策は実施していない。

[全候補の台帳](../experiments/search-candidates-20261002.json)には、4get、DuckDuckGo、Google、Bing、Yahoo! JAPAN、Startpage、Brave、Mwmbl、Wiby、公開SearXNG、Mojeek、Crossref、Serper、SerpApi、Google Custom Search JSON APIを収録した。有料・新規受付終了・今回取得不能の候補も理由とともに残す。公開SearXNGは6サイト、4getは2サイトを個別に記録した。

[全保存ZIPと復元方法](../experiments/snapshots/README.md)には、候補台帳、5runのチェックポイント、取得資料と発見リンクの一覧、出典付き回答、保存時点のコード・テスト・手順書、ハッシュ台帳を含める。チェックポイント内にはstate、要求、回答、元の取得データ、抽出本文、予算・失敗の履歴を保持する。取得資料一覧には確認画面や書誌メタデータも種別を付けて残し、検索結果本文と区別する。元の `.lab-output/search-discovery-all-20261002.zip` と同じ内容のZIPをリポジトリへ保存し、全5runの復元検証を通した。

## 日本語検索の主力を選ぶ基準

ユーザーの最新基準は、精密な比較を行わなくても日本語ページの精度・網羅性を期待する根拠があること。取得できるだけでは主力採用の根拠として足りない。GoogleやBingの検索結果を使う入口を優先し、Mwmbl・Wibyは補助候補とする。これは運用上の選択基準で、Googleと同等であるという実測認定ではない。

[DuckDuckGoの公式説明](https://duckduckgo.com/duckduckgo-help-pages/results/sources)は、通常のリンクと画像を主にBingから供給すると述べる。

> traditional links and images in our search results too, which we largely source from Bing.

既存のDuckDuckGo HTML入口は実地取得済みで、この供給元の説明から大規模な検索基盤を使う主力候補として残す。日本語の順位・鮮度・網羅性がGoogleと同一とは扱わない。

追加確認のrunは`.lab-output/japanese-search-eligibility-20261002`。「国立国会図書館」でGoogle指定の4get.ca、4get.ch、公開SearXNGのsearch.disroot.orgを試した。4get.caはGoogleを選べる画面を返したが、IQテストの案内が表示され、検索結果は取得できていない。4get.chは環境proxyのCONNECT 403、DisrootはHTTP 429だった。これらはGoogle由来の無料入口を確保できたという結果ではない。Startpageのabout-us本文では供給元を確認できなかった。

供給元と取得可能性の確認を続け、Google由来と確認できた入口でも実際の結果を取得できるまでは主力にしない。取得確認画面やドロップダウンのエンジン名を、検索結果そのものと数えない。

この追加確認には先の40 HTTP・1,330,018 bytesを引き継ぎ、3run合計58 HTTP・2,920,978 budget-charged bytesで終了した。最初の全体上限60 HTTP・24MB以内。保存本文の先頭6000文字がナビゲーションで占められていたため、通信を増やさず末尾を再観測する`review-tail`を追加し、既存要求を保持して供給元の説明を読めるようにした。58件のテストが通過した。

## 実際に取得できた入口

| 候補 | 試した検索URL | 今回確認できた内容 |
| --- | --- | --- |
| Mwmbl | <https://mwmbl.org/search?q=library> | 検索結果の本文とHTTPSリンク。`library`の1語だけを確認。 |
| Wiby | <https://wiby.me/?q=library>、<https://wiby.me/?q=citation+chasing> | 両検索の本文とHTTPSリンク。HTTPの結果リンクもあり、それは現在のHTTPS限定実行器では追わない。 |

Mwmbl、Wibyとも今回の取得にはAPIキー、ログイン、ブラウザ描画が不要だった。Google相当の品質・日本語検索・長期安定性は評価していない。`citation chasing`のWiby結果には関係の薄いページも含まれる。検索結果の取得成功と、調査に役立つ資料の発見は分けて評価する。

Mwmblはルートの`/?q=...`から`/search?q=...`へリダイレクトする。空白を含む検索語のルートURLでは、Locationに未エンコードの空白が入り、この実行器は拒否した。追加providerは`/search`へ直接送る。空白を含む語での直接URLのライブ取得は今回未検証。

## 検索結果を取得できなかった候補

| 候補 | 検索URLまたはorigin | 今回の結果 |
| --- | --- | --- |
| Google | `https://www.google.com/search?q=library` | robotsで拒否し、検索ページへのHTTPは送信していない。 |
| Bing | `https://www.bing.com/search?q=library` | 同上。 |
| Yahoo! JAPAN | `https://search.yahoo.co.jp/search?p=library` | 同上。 |
| Startpage | `https://www.startpage.com/sp/search?query=library` | 同上。 |
| Brave | `https://search.brave.com/search?q=library` | 同上。 |
| 公開SearXNG | `https://searx.be/search?q=library` | HTTP 200のブラウザ確認画面。検索結果の取得成功には数えない。 |
| 公開SearXNG | `https://search.inetol.net/search?q=library` | HTTP 200のJavaScriptを求める確認画面。検索結果なし。 |
| 公開SearXNG | `https://searx.tiekoetter.com/search?q=library` | HTTP 429。 |
| 公開SearXNG | `https://search.ononoki.org/search?q=library` | robots取得時に環境proxyのCONNECT 403。対象サイトからのHTTP 403と区別する。 |
| 公開SearXNG | `https://search.sapti.me/search?q=library` | robots取得不能。検索ページは未取得。 |

これは識別用User-Agent、urllib、当該Cloud経路、実行時点での結果。各サービスが世界中の環境で取得不能であるとは言えない。この表のSearXNGは最初の5サイトで、上記の日本語確認でDisrootを加え、保存台帳には合計6サイトを残した。

## 試作から使う

```bash
python3 -B -m jse.lab topic \
  --run .lab-output/my-free-search \
  --topic '調べたいこと' \
  --query '初期検索語' \
  --provider mwmbl --provider wiby
```

以後の計画・取得・Luna回答・引用照合・保存は[テーマ探索の手順](topic-discovery.md)と同じ。2providerは選択式で、既定のDuckDuckGoとCrossrefは変更していない。

## 証跡と上限

初回runは`.lab-output/free-search-engines-20261002`、追加確認は`.lab-output/free-searxng-followup-20261002`。追加確認には初回の28 HTTP・310,868 bytesをobjectiveと判断noteへ引き継ぎ、両run合計でも最初に設定した60 HTTP・24 MB以内に収まる上限を割り当てた。失敗履歴や予約済みbytesは戻していない。

合計40 HTTP・1,330,018 budget-charged bytes。10成功HTMLのうち、検索結果本文は6件（両armの重複取得を含む）、確認画面は4件。HTTP成功ページ数をそのまま検索成功数にはしない。BFS/Lunaはこの確認では同じseedを別取得しただけで、選択方法の優位性は評価していない。

56件の`test_lab*.py`テストが通過した。新providerの検索URL生成と、結果リンクから別サイトの本文へ辿る処理はfixtureで確認した。両runの`verify`・`export`を行い、state、requests、answers、取得本文をチェックポイントに保存した。
