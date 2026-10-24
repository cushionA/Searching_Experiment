# private CPU runtime識別情報の調査

固定ref `superbigzabuton/issue24-cpu-session-probe-20261024` version 1 はCPU、private、Internet無効、上限180秒でcomplete。限定した環境変数名の存在と数値形式だけを調べ、認証情報や任意の環境変数値は出力していない。

runtime-identity.jsonではsession IDを取得できなかった。この結果から値を推測せず、API停止の実測は別の明示許可された公開テストで行った。privateでの実行・状態照会自体にブラウザーログインは不要。
