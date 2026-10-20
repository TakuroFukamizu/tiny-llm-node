# Game AI Backend — API handoff (Pi 5 + RTX 4060)

**Language:** English | [日本語](game_ai_backend_ja.md)

Client-facing reference for using the node as an AI backend for a game (e.g. a
Unity client on another machine). The server side — build, models, systemd — is
in [llm_serving.md](llm_serving.md). Originally built for a hackathon.

Replace `<NODE_IP>` below with the node's LAN address (`hostname -I` on the
node; it was `192.168.100.143` on ours).

## Endpoints

| Port | Model | Use | Speed |
|---|---|---|---|
| `http://<NODE_IP>:8080` | Qwen2.5-3B-Instruct | NPC dialogue, JSON/structured output, function-calling | ~75 tok/s |
| `http://<NODE_IP>:8081` | Qwen2.5-1.5B-Instruct | fast intent/slot classification, short replies | ~140 tok/s |

OpenAI-compatible paths on each: `/v1/chat/completions`, `/v1/completions`,
`/v1/models`, `/health`. A web test UI is served at `/`.

## Call example (OpenAI-compatible)

```bash
curl http://<NODE_IP>:8080/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "messages":[
    {"role":"system","content":"You are an RPG engine. Respond ONLY as JSON: {\"npc_line\":string,\"emotion\":string,\"action\":string}."},
    {"role":"user","content":"The player offers the blacksmith a rare ore."}],
  "max_tokens":120, "temperature":0.7
}'
```

Returns the content in `choices[0].message.content`. Add `"stream":true` for
token-by-token SSE.

## Unity (UnityWebRequest)

POST the same JSON body to `http://<NODE_IP>:8080/v1/chat/completions` with
header `Content-Type: application/json`, and parse
`choices[0].message.content`. For structured game output, instruct JSON in the
system prompt (works well on the 3B) — or use `response_format` / a GBNF grammar
for hard guarantees.

```csharp
// sketch
var body = "{\"messages\":[{\"role\":\"system\",\"content\":\"...\"}," +
           "{\"role\":\"user\",\"content\":\"" + utterance + "\"}]," +
           "\"max_tokens\":120,\"temperature\":0.7}";
using var req = new UnityWebRequest("http://<NODE_IP>:8080/v1/chat/completions", "POST");
req.uploadHandler   = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
req.downloadHandler = new DownloadHandlerBuffer();
req.SetRequestHeader("Content-Type", "application/json");
yield return req.SendWebRequest();
// parse req.downloadHandler.text -> choices[0].message.content
```

## Fast intent classification (8081)

```
system: "Classify the player utterance into exactly one intent: ATTACK, TALK, TRADE, FLEE, EXPLORE. Examples: 'I swing my axe'->ATTACK; 'Good morning'->TALK; 'What do you sell?'->TRADE; 'I run'->FLEE; 'Let me look around'->EXPLORE. Reply ONLY the label."
user:   "<player utterance>"
max_tokens: 5, temperature: 0
```

Verified correct on ATTACK / TALK / TRADE / FLEE / EXPLORE at ~140 tok/s.

## Notes for the client

- **LAN-only**, HTTP (not HTTPS). Keep the Unity box on the same subnet; don't
  port-forward these ports.
- To require a key, the server can add `--api-key SECRET`; then send
  `Authorization: Bearer SECRET` from Unity.
- The backend auto-restarts on crash and on boot, so a transient blip recovers
  on its own within a few seconds — retry failed requests.
- Both models are small and VRAM-resident; latency is dominated by output
  length. Keep `max_tokens` tight for fast game-loop calls.
