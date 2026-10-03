# ブラウザ・fetchの共通プロキシ設定

`scripts/with_proxy.py` を既存コマンドの前に付けると、その子プロセスだけに共通プロキシを渡す。Python 3.12以上、追加パッケージ不要。シェル全体の `HTTPS_PROXY` やCA設定は変更しない。

以下の `proxy.example:3128` は説明用の架空アドレス。実行時は自分で管理・利用許可されたプロキシに置き換える。

## ローカルでのプロキシ経由テスト

```bash
python3 -B -m unittest tests.test_lab_proxy_launcher tests.test_lab_proxy_integration -v
```

テスト自身が `127.0.0.1` の空きポートに一時プロキシと取得先を作り、終了時に停止する。転送先はローカルfixtureに固定し、公開プロキシや実サイトには接続しない。通常のHTTP転送、HTTPSのCONNECT、実際の `jse.lab.LiveTransport`、診断のPlaywrightブラウザとrobots用HTTPクライアントを検証する。HTTPSは一時証明書だけをテストプロセスに信頼させ、証明書・ホスト名照合を有効に保つ。

HTTPSテストには `openssl`、ブラウザの実測にはNode・`chromium`・診断用Playwrightの配備が必要。未配備の場合は該当テストをskipとして明示する。fetchと設定テストだけの成功を全ブラウザの実測とは扱わない。Patchright・Lightpanda・wreq-js・Impitそれぞれの外部プロキシ経由実測は別途必要。

2026-10-03、このCloudでlocalhost用ソケットの実行許可を付け、上記Playwright/robots・HTTPS CONNECTの2統合テストが成功した。外部プロキシへの接続を許可・検証した結果ではない。

自己運用する代替候補は [Tinyproxy](https://github.com/tinyproxy/tinyproxy)。公式[README](https://github.com/tinyproxy/tinyproxy/blob/master/README.md)と[CONNECT実装](https://github.com/tinyproxy/tinyproxy/blob/master/src/reqs.c)を確認した。GCP VMのHTTP/CONNECT forward proxyとして検討できるが、今回Tinyproxyの導入・稼働検証はしていない。上記テストで使うのは本リポジトリ内の小さなfixture用プロキシであり、運用サーバーとして公開しない。

## 通常のPC・GCP VMで使う

まず設定を確認する。このコマンドは通信せず、プロキシへの疎通成功を意味しない。

```bash
python3 -B scripts/with_proxy.py \
  --proxy http://proxy.example:3128 --check
```

ブラウザ・fetchを含む診断の計画を表示する例（通信なし）:

```bash
python3 -B scripts/with_proxy.py \
  --proxy http://proxy.example:3128 \
  -- node experiments/bot-diagnostics/framework.mjs plan
```

実サイト取得は、対象・上限が承認済みの設定とrunに限って従来の実行器から行う。HTTP取得とブラウザ描画のどちらでも入口は同じ。

```bash
# 既存の承認済みrun。transport=live / impit / adaptiveに共通
python3 -B scripts/with_proxy.py \
  --proxy http://proxy.example:3128 \
  -- python3 -B -m jse.lab run --run lab-runs/YOUR_APPROVED_RUN

# 選択した診断シナリオと予算を確認・承認した後、新しい出力先へ実行
python3 -B scripts/with_proxy.py \
  --proxy http://proxy.example:3128 \
  -- node experiments/bot-diagnostics/framework.mjs run lab-runs/YOUR_NEW_RUN --sites=YOUR_SITES_FILE
```

繰り返し使う場合は `JSE_PROXY_URL` に同じURLを設定し、`--proxy` を省略できる。`--proxy` が優先する。両方とも未指定なら既存の環境設定をそのまま継承する。ツールごとに違うプロキシを使う場合も、起動コマンドごとに指定する。

| 対象 | プロキシの渡し先 |
|---|---|
| jse.lab の urllib / Impit | HTTPS用の環境プロキシ |
| jse.lab の adaptive | 予算管理下のImpit。ブラウザ自身はofflineのまま |
| 診断の wreq-js / Impit | HTTPクライアントのプロキシ設定 |
| 診断の Playwright / Patchright | ブラウザ起動設定とHTTPクライアント（grounding時のrobots取得など） |
| 診断の Lightpanda | `--http-proxy` とHTTPクライアント（grounding時のrobots取得など） |

明示指定時は `HTTP_PROXY` / `HTTPS_PROXY` と小文字の同名変数をそろえ、競合する `ALL_PROXY` / `all_proxy` を子環境から除く。`NO_PROXY` / `no_proxy` は維持するため、対象が除外されていないか確認する。各ツールの除外指定の対応は同一ではない。診断のChromiumはlocalhostを除外し、ローカルfixtureのHTTPクライアントも別経路を使う。

今回の共通設定は、ユーザー名・パスワードを含まないHTTP(S)プロキシURLを対象にする。認証情報入りURL、SOCKS、URLのパス・クエリ等は受け付けない。IP制限やVPNで利用者を制限する構成向け。認証が必要なプロキシは各クライアントの対応を別途そろえる必要がある。

HTTPSサイト向けにも `http://proxy.example:3128` を指定できる。プロキシがCONNECTに対応していることが条件。TLS検証と配布済みCAは維持する。ローカルfixture専用の `browser_benchmark.py` は外部プロキシの動作確認には使わない。ChatGPT標準ツールの設定を変更する機能ではない。

## 現在のCodex Cloudでの制約

この管理環境では既存のsidecarプロキシを利用する。`/etc/codex/network-policy.json` が存在する環境では、共通ランチャーは継承プロキシから別の宛先への変更を拒否する。`--check` でも理由を返す。既存プロキシと同じ指定は環境を変更せず実行する。ポリシーが不明・不正な場合も変更しない。

2026-10-03の確認では、このスレッドのHTTPネットワーク設定は `disabled` / `enforced`。起動時スナップショットはVPN未設定、HTTP・TCPの許可先とも空だった。指定のGCPプロキシへの実接続は未検証。GCPでポートを開けるだけでは、このCloudからの経路は作れない。

すぐに利用する場合は、外部プロキシへの接続が許可された通常のGCP VMで本リポジトリを実行する。現在のCloudから使う場合は、環境の管理画面でネットワーク経路を設定する必要がある。HTTP許可ドメインの追加だけで、任意の外部プロキシを上流にできるとは限らない。

管理環境で提供されるVPN/TCP転送を選ぶ場合は、GCP側のTailscale等の接続、Cloud側のVPN設定と宛先TCP grant、必要に応じた環境の作り直しが必要になる。その経路の疎通確認とブラウザへの接続は今回のランチャーには含まない。ポリシーファイルの書き換え・既存proxyの解除・TLS検証の無効化で接続しない。

## GCP側で確認すること

既存プロキシのVMへSSHし、待受を確認する。

```bash
sudo ss -lntp 'sport = :3128'
```

- 同じVMで取得ツールも動かす場合は、到達可能なら `http://127.0.0.1:3128` を指定できる。外向けのポート開放は不要。
- 別VMやPCから使う場合は、そのインターフェイスで待ち受け、VPC・OS両方のファイアウォールとプロキシ自身の接続元ACLで許可する。localhostだけの待受では別VMから接続できない。
- `proxy.example` が正しい接続先を指すこと、HTTPS宛てのCONNECT（通常443）が許可されること、VMから対象サイトへの外向き通信が可能なことを確認する。

公開IP経由で接続する場合のGCPファイアウォール例。値は実際のproject・network・VM・zone・接続元固定IPに置き換える。対象VM専用のタグを使い、既存の同等ルールがあれば新設せず確認する。

```bash
GCP_PROJECT='YOUR_PROJECT_ID'
GCP_NETWORK='YOUR_VPC_NETWORK'
GCP_ZONE='YOUR_ZONE'
PROXY_VM='YOUR_PROXY_VM'
CLIENT_CIDR='YOUR_CLIENT_EGRESS_IPV4/32'

gcloud compute instances add-tags "$PROXY_VM" \
  --project="$GCP_PROJECT" --zone="$GCP_ZONE" --tags=searching-proxy-3128

gcloud compute firewall-rules create searching-proxy-3128 \
  --project="$GCP_PROJECT" --network="$GCP_NETWORK" \
  --direction=INGRESS --action=ALLOW --rules=tcp:3128 \
  --source-ranges="$CLIENT_CIDR" --target-tags=searching-proxy-3128
```

接続元は実際にプロキシVMから見える送信元IP。Cloud側の固定出口IPは未確認なので推測して指定しない。接続元が決まるまでは `0.0.0.0/0` に開放しない。内部IP/VPNを使う場合は、その経路の送信元とACLで制限する。

疎通確認も承認済みrunの予算内で行う。jse.labでは `diagnose --run ... --arm bfs --client urllib`（または `impit`）を共通ランチャー経由で実行し、同じrunの台帳へ記録する。接続拒否・タイムアウト、407認証エラー、CONNECT拒否、証明書エラー、対象サイトからのHTTPエラーを分けて確認する。
