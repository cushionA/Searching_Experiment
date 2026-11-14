# クラウド途中解析

上流503をサイト側拒否・アルゴリズム失敗へ割り当てない。status=completedは実行終了であり、到達・有効HTML保存の成功とは別。

- B1: navigation error分類 {'click_timeout': 22, 'saved_path_link_missing': 6}。503ページ記録 50、raw HTML未保存 18。
- C1: navigation error分類 {'click_timeout': 9, 'document_budget': 1}。503ページ記録 47、raw HTML未保存 27。

C2追加は保留。通信障害・UIクリック失敗・経路上のリンク消失をモデルが直すとは仮定しない。候補選択だけの改善余地を、200かつ有効HTMLの観測へ限定して検証してから追加する。

最小Skill統合案: 既存parser_flowのAST結果は起点・候補・遷移型のヒントとして保持し、予算付き非LLM実行器、凍結rules/graph/path、raw HTML/DOM、状態・費用台帳、独立G/R採点を別部品として接続する。追加ページを毎回LLMへ送らない。元Skillのデプロイ命令は実行しない。
