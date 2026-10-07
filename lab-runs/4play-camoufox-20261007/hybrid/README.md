# Camoufoxと4playの検証資料

この資料は4play・Camoufoxの固定50サイト結果と、5サイト検索・4件のブロッキングsubsetによるhybrid検証を保存します。固定50表では、subset外の45サイトを未測定として扱います。候補リンクはDOM上のクエリ語一致で抽出しており、意味的な関連性や正解率を保証しません。単発の観測から検知耐性の改善は確認できません。

## 実装と観測の対応

このPRの現行コードでは、既存の `4play` backendはloopback bridgeを維持し、hybrid制御用に移植した `fourplay-native-runtime.mjs` を別backendとして追加しています。移植後のNodeテストは `node --test experiments/bot-diagnostics/fourplay-native-runtime.test.mjs` で実行できます。

保存された観測・条件はcheckout commit `fd72ef7f305ad1efa765c390e0fc9939ea331cca` のもとで取得したもので、PRの移植後runtimeそのものを測定した記録ではありません。hybridの観測時はCamoufox 0.12.0 / Firefox 152.0.4-beta.30、`allowAddonNewtab=true`、元4play拡張の無変更 `bg.js` を使用しました。測定条件、provenance、検証記録はそれぞれ同じディレクトリに保存しています。

## チェックポイント

完全な証拠checkpoint `4play-camoufox-20261007-checkpoint.zip` はローカル保存で、Gitには含めていません。サイズは346,639,146 bytes、SHA-256は `2260fc5e5321bd4e1176d9889b77cd001c2913228ff9afcbe691391a0f3ef4b4` です。139 runの検証済みで、`runtime-state`（NSS証明書DBなど）や一時的な実行状態は除外されています。詳細は [`infrastructure/checkpoint.json`](../infrastructure/checkpoint.json) を参照してください。

チェックポイントはローカル実験の再開・証拠保全用です。PRにはsnapshot表、集計、条件、provenance、検証metadataのみを含め、raw DOM/ledger、debug記録、拡張コピー、依存lockfile、ZIPは含めません。
