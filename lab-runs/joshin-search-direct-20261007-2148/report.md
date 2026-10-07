# Joshin検索・入力診断 横断レポート

8つのrunの`summary.json`と`checkpoint.json`を照合した記録です。HTTP拒否は0件検索と数えず、200で検索DOMを得た観測と全ページを走査した観測を分けています。

| 観測 | 結果 | 出典 |
|---|---|---|
| 直接経路の初回3方式比較 | Camoufox、通常4play、アセンブル版はすべてホーム200・検索403。 | [summary](summary.json)、[条件](camoufox/conditions.json) |
| 入力診断の言語対照 | JAは7操作すべてHTTP 200で検索DOMを取得。ENは7操作すべてHTTP 403。最終DOM対照はJA→EN→JAで200→403→200。 | [summary](../joshin-input-diagnostics-20261007-2215/summary.json)、[JA条件](../joshin-input-diagnostics-20261007-2215/live-dom-ja/conditions.json)、[EN条件](../joshin-input-diagnostics-20261007-2215/live-dom-en/conditions.json) |
| JA単独ブラウザー観測 | Camoufoxと通常4playはともにHTTP 200で検索DOMを取得。各観測は1ページのみで、全件走査ではありません。 | [Camoufox summary](../joshin-camoufox-ja-20261007-2308/summary.json)、[条件](../joshin-camoufox-ja-20261007-2308/live-camoufox-ja/conditions.json)、[4play summary](../joshin-fourplay-ja-20261007-2318/summary.json)、[条件](../joshin-fourplay-ja-20261007-2318/live-fourplay-ja/conditions.json) |
| proxy経由のJA観測 | Camoufox、4play、組み合わせ方式の3経路はすべてHTTP 403。SquidログにCONNECT記録がなく、経路を個別に独立確認できていないため、403の原因をproxyと断定できません。 | [summary](../joshin-proxy-ja-20261007-2340/summary.json)、[条件](../joshin-proxy-ja-20261007-2340/live-camoufox-ja/conditions.json) |
| 全ページのページ送り | 組み合わせ方式と単独Camoufoxは、それぞれ84ページをHTTP 200で走査し、表示総数3,343件・ユニーク商品URL 3,343件を記録。各83回のページ送りで、サイト表示の最終ページ・総数に到達しました。 | [組み合わせ方式 summary](../joshin-assembled-pagination-20261008-0005/summary.json)、[条件](../joshin-assembled-pagination-20261008-0005/live/conditions.json)、[Camoufox summary](../joshin-camoufox-pagination-20261008-0038/summary.json)、[条件](../joshin-camoufox-pagination-20261008-0038/live-recheck/conditions.json) |
| Patchright smoke test | JA検索はHTTP 404、EN検索はHTTP 403。トップページは両方200。JAは最大3ページ、ENは1ページの試行条件です。 | [summary](../joshin-patchright-smoke-20261008-0116/summary.json)、[JA条件](../joshin-patchright-smoke-20261008-0116/live-ja/conditions.json)、[EN条件](../joshin-patchright-smoke-20261008-0116/live-en/conditions.json) |

## 読み方と限界

入力診断の7対7は、このrunに保存された逐次観測です。因果関係、一般的な成功率、サイト側の判定規則を示しません。単独Camoufox・4playの200は検索結果DOMの取得を示し、全カタログの取得を意味しません。全件走査の結論はページ送り2runに限ります。Patchrightの404/403とその他の403は0件結果ではありません。

全8件の`source_commit`は同じHEAD識別子を記録しています。これは作業ツリーが無変更だった証明ではありません。run時の作業差分の記録、ZIP内の`replay-source`実体、各実装ファイルのSHA256は[出所index](evidence-index.json)にまとめました。既存の`source.patch`と`source-manifest.json`は履歴として保持し、書き換えていません。PRのcleanup変更は実測後の整理です。

検証済みcheckpoint ZIPとraw証拠は既存の`lab-runs`位置に保持します。Gitにはこの横断レポートと軽量な出所indexを記録し、巨大ZIP・生HTML・blobs・画像は追加しません。
