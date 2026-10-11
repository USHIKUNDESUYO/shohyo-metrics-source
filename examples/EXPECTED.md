# 架空の入力例で確認する出力

`kind`は`extraction`、`dataset.cases`は2です。`failures.incorrect_cases`は1、`failures.field_failures.vendor`は1になります。

`quality_gate.status`は`human_review_required`です。最小の項目正解率が0.5で、確認が必要な1件があります。終了コードは0なので、後続処理ではJSONの状態も確認してください。

`speed.avg_elapsed_sec`と`speed.max_elapsed_sec`は`null`です。時間の測定は行っていません。来歴のハッシュやモデル名も未入力のため`null`になります。

この2件と正解率は、出力の読み方を確認するために作った例です。実際のモデル精度、利用者の成果、90％時短の根拠には使用しません。
