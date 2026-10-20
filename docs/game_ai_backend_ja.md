# ゲーム AI バックエンド — API ハンドオフ(Pi 5 + RTX 4060)

**Language:** [English](game_ai_backend.md) | 日本語

このノードをゲーム用 AI バックエンド(例: 別マシンの Unity クライアント)として
使うための、クライアント向けリファレンス。サーバ側(ビルド・モデル・systemd)は
[llm_serving_ja.md](llm_serving_ja.md)。元はハッカソン用に構築したもの。

以下の `<NODE_IP>` はノードの LAN アドレスに置き換える(ノードで `hostname -I`。
本機では `192.168.100.143` だった)。

## エンドポイント

| ポート | モデル | 用途 | 速度 |
|---|---|---|---|
| `http://<NODE_IP>:8080` | Qwen2.5-3B-Instruct | NPC 対話・JSON/構造化出力・function calling | ~75 tok/s |
| `http://<NODE_IP>:8081` | Qwen2.5-1.5B-Instruct | 高速な意図/スロット分類・短い応答 | ~140 tok/s |

各ポートの OpenAI 互換パス: `/v1/chat/completions`, `/v1/completions`,
`/v1/models`, `/health`。`/` にテスト用 Web UI。

## 呼び出し例(OpenAI 互換)

```bash
curl http://<NODE_IP>:8080/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "messages":[
    {"role":"system","content":"You are an RPG engine. Respond ONLY as JSON: {\"npc_line\":string,\"emotion\":string,\"action\":string}."},
    {"role":"user","content":"The player offers the blacksmith a rare ore."}],
  "max_tokens":120, "temperature":0.7
}'
```

内容は `choices[0].message.content` に入る。`"stream":true` でトークン逐次(SSE)。

## Unity(UnityWebRequest)

同じ JSON ボディを `http://<NODE_IP>:8080/v1/chat/completions` に POST(ヘッダ
`Content-Type: application/json`)し、`choices[0].message.content` をパースする。
構造化出力は system プロンプトで JSON を指示すれば 3B でも十分機能する。厳密に
保証したいなら `response_format` / GBNF grammar を使う。

```csharp
// スケッチ
var body = "{\"messages\":[{\"role\":\"system\",\"content\":\"...\"}," +
           "{\"role\":\"user\",\"content\":\"" + utterance + "\"}]," +
           "\"max_tokens\":120,\"temperature\":0.7}";
using var req = new UnityWebRequest("http://<NODE_IP>:8080/v1/chat/completions", "POST");
req.uploadHandler   = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
req.downloadHandler = new DownloadHandlerBuffer();
req.SetRequestHeader("Content-Type", "application/json");
yield return req.SendWebRequest();
// req.downloadHandler.text をパース -> choices[0].message.content
```

## 高速な意図分類(8081)

```
system: "Classify the player utterance into exactly one intent: ATTACK, TALK, TRADE, FLEE, EXPLORE. Examples: 'I swing my axe'->ATTACK; 'Good morning'->TALK; 'What do you sell?'->TRADE; 'I run'->FLEE; 'Let me look around'->EXPLORE. Reply ONLY the label."
user:   "<player utterance>"
max_tokens: 5, temperature: 0
```

ATTACK / TALK / TRADE / FLEE / EXPLORE で正答確認済み、~140 tok/s。

## クライアント側の注意

- **LAN 限定**・HTTP(HTTPS ではない)。Unity 機を同一サブネットに置き、これらの
  ポートはポートフォワードしない。
- キーを要求するなら、サーバ側で `--api-key SECRET` を追加し、Unity から
  `Authorization: Bearer SECRET` を送る。
- バックエンドはクラッシュ時・起動時に自動再起動するので、一時的な断は数秒で自己
  復帰する — 失敗したリクエストはリトライする。
- 両モデルとも小型で VRAM 常駐。レイテンシは出力長に支配されるので、ゲームループ
  用途では `max_tokens` を小さく保つ。
