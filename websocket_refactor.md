# RN-Android PCM 语音通话式 AI Teacher：WebSocket 整体前后端方案（语音 + 字幕 + 板书 + 用户实时 ASR）

目标：做成“和大模型语音通话”的状态机。前端尽量薄：持续上行 PCM、播放 TTS、渲染字幕/板书/ASR。后端权威：VAD/端点、回声门控、ASR、LLM 分段、TTS 流式下行、事件驱动 UI。

# 预期效果

- 前端几乎无状态（拿后端返回值做渲染，自己会需要缓冲部分内容）
- 前端持续检测和发送音频
- 用户说话-后端决定asr-进行asr-实时返回transcript给前端
- 用户说话结束-后端识别到结束，asr文本结果传输给agent-agent返回块状回应-[S] [/S] [B] [/B] （S是speech，B是板书，S+B是一个segment），或者[S] [/S] [S] [/S] （没有板书，一个[S] [/S]一个segment）
- [S] [/S]中的内容需要发送给tts，tts流式返回。返回给前端audio和增量式的aiText。在当前segment的speech结束的时候，给前端发送board的内容，前端按照拿到的内容做渲染（为了保证音频流畅，会有一个小的缓冲区）
- 如果在播放的过程中用户打断，说话，那么就停止说话，回应用户。
- 
---

## 1. 角色与职责

### 前端（RN-Android）
- 建立 WebSocket 连接；发送 client_hello / client_ready
- 持续采集麦克风 PCM（16kHz, mono, s16le），按固定帧（推荐 20ms=320 samples=640 bytes）上行
- 播放后端下行的 TTS PCM（AudioTrack 队列）
- 渲染：
  - AI 字幕（ai_text_delta 逐步追加）
  - 用户实时 ASR（asr_partial / asr_final）
  - 板书（board，按 segment 结束后显示）
- 最小运行态（不可避免，但可丢弃）：录音帧 seq、播放队列、当前 segment 的文本累积、UI 当前 state

### 后端（权威状态机）
- 管理会话状态：INIT → LISTENING / PROCESSING / SPEAKING（支持 interrupt）
- 音频输入：VAD/endpointing（例如 1.5s 静音触发 finalize）
- 回声/串扰门控（建议：SPEAKING 时降低 ASR/VAD 触发；必要时丢弃疑似回声帧）
- ASR（流式）：产出 asr_partial / asr_final
- LLM：对用户 utterance 调用，生成分段 segment（speech + board）
- TTS：对每个 segment.speech 产出 PCM chunk 流式下行
- 同时产出 ai_text_delta（节拍器式增量，达到“大致同步”字幕观感）
- 在 audio_end 后发送 board（让板书“接近说完再出现”）

---

## 2. 音频与时间参数（建议固定）
- 采样率：16000 Hz
- 通道：1
- 采样格式：PCM s16le（16-bit little endian）
- 上行帧长：20ms（推荐）  
  - samples_per_frame = 16000 * 0.02 = 320 samples
  - bytes_per_frame = 320 * 2 = 640 bytes
- 下行 TTS chunk：可以与上行同帧长（20ms/40ms），越短越低延迟，但 WS 消息更频繁；建议 40–80ms 作为折中（取决于 TTS 服务粒度）

---

## 3. WebSocket 消息总结构（统一 Envelope）

所有消息统一 JSON：

```json
{
  "type": "xxx",
  "conv_id": "c123",
  "msg_id": "uuid-or-snowflake",
  "ts_ms": 1730000000000,
  "payload": { }
}

关键约束：
	•	音频 chunk 必须带 seq（递增）
	•	AI 输出必须带 segment_id
	•	每个 segment 必须有明确 audio_end
	•	ASR 使用 stream_id（一次用户发言的音频流）

⸻

4. Client -> Server（上行消息）

4.1 client_hello（建立连接后第一条）

{
  "type": "client_hello",
  "conv_id": "c123",
  "payload": {
    "client": "rn-android",
    "app_version": "1.0.0",
    "audio": {
      "format": "pcm_s16le",
      "sample_rate": 16000,
      "channels": 1,
      "bits_per_sample": 16,
      "frame_ms": 20
    },
    "capabilities": {
      "asr_partial": true,
      "ai_text_delta": true,
      "board": true,
      "interrupt": true
    }
  }
}

4.2 mic_start（进入页面后自动开始；或用户点开始）

{
  "type": "mic_start",
  "conv_id": "c123",
  "payload": {
    "stream_id": "u001"
  }
}

4.3 user_audio_chunk（持续发送音频帧）
	•	建议“麦克风开启期间持续上行”，不靠前端 VAD 决定是否发送（可选：静音降频，但不作为唯一依据）

{
  "type": "user_audio_chunk",
  "conv_id": "c123",
  "payload": {
    "stream_id": "u001",
    "seq": 123,
    "format": "pcm_s16le",
    "sample_rate": 16000,
    "channels": 1,
    "bits_per_sample": 16,
    "data_b64": "....",
    "vad_hint": "speech" 
  }
}

4.4 mic_end（可选；若你完全靠后端 1.5s 静音截断，可不发）

{
  "type": "mic_end",
  "conv_id": "c123",
  "payload": {
    "stream_id": "u001",
    "last_seq": 456
  }
}

4.5 interrupt（用户打断：停止当前 TTS/LLM，回到 LISTENING）

{
  "type": "interrupt",
  "conv_id": "c123",
  "payload": { "reason": "user_tap" }
}


⸻

5. Server -> Client（下行消息）

5.1 state（权威状态）

{
  "type": "state",
  "conv_id": "c123",
  "payload": {
    "state": "idle | listening | processing | speaking",
    "detail": "optional"
  }
}

5.2 session_greeting（进入页面第一句话由 LLM 触发）

建议：client_hello 后，后端立即进入 PROCESSING → SPEAKING，并推送一个 segment（无需等用户说话）。

{
  "type": "session_greeting",
  "conv_id": "c123",
  "payload": {
    "triggered": true
  }
}

（也可以不需要这个 type，直接开始发 segment_start 即可。）

5.3 asr_partial / asr_final（用户实时字幕）

{
  "type": "asr_partial",
  "conv_id": "c123",
  "payload": {
    "stream_id": "u001",
    "text": "我想问一下…",
    "stability": 0.6
  }
}

{
  "type": "asr_final",
  "conv_id": "c123",
  "payload": {
    "stream_id": "u001",
    "text": "我想问一下这道题怎么做"
  }
}

5.4 segment_start（AI 输出分段开始）

{
  "type": "segment_start",
  "conv_id": "c123",
  "payload": {
    "segment_id": "s0001",
    "index": 0
  }
}

5.5 ai_text_delta（AI 字幕增量：实现“边说边出”观感）

后端对 segment.speech 做节拍式切片（例如每 120ms 推 2–6 个字），不追求精确对齐。

{
  "type": "ai_text_delta",
  "conv_id": "c123",
  "payload": {
    "segment_id": "s0001",
    "seq": 10,
    "delta": "我们先看题目"
  }
}

5.6 audio_chunk（TTS PCM chunk）

必须带 seq；前端按 seq 入队 AudioTrack。

{
  "type": "audio_chunk",
  "conv_id": "c123",
  "payload": {
    "segment_id": "s0001",
    "seq": 0,
    "format": "pcm_s16le",
    "sample_rate": 16000,
    "channels": 1,
    "bits_per_sample": 16,
    "data_b64": "...."
  }
}

5.7 audio_end（该 segment 的音频流结束）

{
  "type": "audio_end",
  "conv_id": "c123",
  "payload": {
    "segment_id": "s0001",
    "last_seq": 42
  }
}

5.8 board（板书：在 audio_end 后立即发送以“接近说完再出现”）

{
  "type": "board",
  "conv_id": "c123",
  "payload": {
    "segment_id": "s0001",
    "format": "md",
    "content": "### 板书\n1) 设 …\n2) 代入 …\n"
  }
}

5.9 segment_end（可选）

{
  "type": "segment_end",
  "conv_id": "c123",
  "payload": { "segment_id": "s0001" }
}

5.10 done（一次回答结束）

{
  "type": "done",
  "conv_id": "c123",
  "payload": {
    "total_segments": 3,
    "reason": "completed | interrupted"
  }
}

5.11 error

{
  "type": "error",
  "conv_id": "c123",
  "payload": {
    "code": 5001,
    "message": "TTS failed",
    "retryable": true
  }
}


⸻

6. 典型时序（进入页面 + 第一段欢迎语 + 用户问答）

6.1 进入页面（第一句话由 LLM 触发）
	1.	前端：WS connect
	2.	前端 → 后端：client_hello
	3.	前端 → 后端：mic_start(stream_id=u001)（可立即开始上行）
	4.	后端 → 前端：state(listening)
	5.	后端（触发欢迎语）→ 前端：
	•	state(processing)
	•	segment_start(s0001)
	•	并行：ai_text_delta + audio_chunk…
	•	audio_end(s0001)
	•	board(s0001)
	•	done
	•	state(idle 或 listening)

6.2 用户说话（实时 ASR）→ 静音 1.5s → 触发 LLM → AI 回复
	1.	前端持续发送 user_audio_chunk(u001, seq=…)
	2.	后端持续回 asr_partial（可选）并在端点触发时回 asr_final
	3.	后端检测 1.5s 静音：进入 state(processing)，调用 LLM
	4.	后端按 segment 流式发：
	•	segment_start
	•	ai_text_delta（节拍器）
	•	audio_chunk（TTS PCM）
	•	audio_end
	•	board
	•	done
	5.	后端回 state(listening)（准备下一轮）

⸻

7. 前端渲染与播放策略（RN-Android）

7.1 数据结构（建议）
	•	segments: Map<segment_id, { aiText: string, board?: any, audioQueue: Map<seq, bytes>, audioEnded: bool }>
	•	uiState: idle/listening/processing/speaking
	•	userAsrText: partial/final

7.2 收到消息的处理规则
	•	state: 更新 UI 状态
	•	asr_partial: 更新“用户正在说”的字幕（覆盖式）
	•	asr_final: 固化用户字幕（追加到对话历史）
	•	segment_start: 创建 segment 视图块；aiText 置空；board 置空；清空该段音频队列
	•	ai_text_delta: 追加到该 segment 的字幕区域（只追加，不回退）
	•	audio_chunk: base64 解码→PCM bytes→按 seq 入播放队列（若连续可直接写入 AudioTrack）
	•	audio_end: 标记该段音频输入结束；允许播放器自然 drain
	•	board: 渲染板书（建议放在该 segment 下方）；如果你希望更“同步”，只在收到 audio_end 后才渲染 board
	•	done: 该轮结束标记（UI 可显示“已回答完毕”或回到 listening）

7.3 AudioTrack 播放（关键点）
	•	建议使用原生模块（你已有 PCMRecorder 类似链路的话，AudioTrack 同理）
	•	采用 MODE_STREAM；维护一个“待播字节队列”
	•	保持小缓冲（例如 200–500ms）避免“板书提前很多”的观感
	•	支持 interrupt：收到用户 interrupt 或后端 state(listening) 时清空待播队列并停止 AudioTrack

⸻

8. 后端实现要点（与你现有代码的最小差异）

你已有“segment + TTS stream”。为了满足本方案，你的后端需要做到：
	1.	音频下行：每个 chunk 立即发送（不要延迟一帧），并加 seq；循环结束发 audio_end
	2.	AI 字幕：增加 ai_text_delta（对 segment.speech 做节拍式切片并与 TTS 并发发送）
	3.	板书：不要在 segment 一开始就发（或发但前端不渲染）；改为在 audio_end 后立刻发送 board
	4.	状态：在第一段 segment 开始前发 state(processing)；第一段开始后发 state(speaking)；结束发 done + state(listening/idle)

⸻

9. 可选增强（不影响最小可用）
	•	client_ack_audio_played：如果未来要“严格说完再出板书”，再加 ACK（当前需求“大致同步”可不做）
	•	opus 上行：节省带宽（但你现在用 PCM 也可以先跑通）
	•	静音降频：前端静音时把 user_audio_chunk 从 20ms 改成 100–200ms 发一帧，并标 vad_hint=silence（后端仍做最终判断）

⸻

10. 最小可交付清单（给工程同学）
	•	WS 消息：新增/确认 type：client_hello, mic_start, user_audio_chunk, interrupt, state, asr_partial, asr_final, segment_start, ai_text_delta, audio_chunk, audio_end, board, done, error
	•	后端：实现 ai_text_delta 节拍器 + audio_end + board 在 audio_end 后发
	•	前端：实现 AudioTrack 播放队列（按 seq），实现 ai_text_delta 追加渲染，asr_partial/final 渲染，board 渲染



# 关键问题：后端何时触发asr
后端必须自己维护一个“会话状态机 + 音频门控策略”，用它来决定：当前收到的 user_audio 帧，是要喂给 ASR（并输出 asr_partial/final），还是应该忽略/弱化（例如正在播 TTS 时把回声当作噪声）。

在你这个“电话式”方案里，最常见的做法不是二选一的“要/不要 ASR”，而是三层门控：状态门控（state）、时间门控（recent playback）、信号门控（能量/相关性）。它们叠加后给出最终决定。

下面是一个可直接落地的规则集，适合你 RN-Android + PCM 上行、后端做 VAD/1.5s 静音终止、允许用户打断（barge-in）的场景。

一、你需要维护的后端状态（每个 conversation）
	1.	state：LISTENING / PROCESSING / SPEAKING
	2.	tts_playing_window：最近一次下行 TTS 的时间窗口（例如 tts_last_chunk_sent_ts，以及预估播放延迟）
	3.	asr_stream_id：当前在识别的用户 stream（如果你按 stream_id 管）
	4.	vad_state：当前是否“在语音段内”（in_speech）、最近一次检测到 speech 的时间、静音累计时长
	5.	barge_in_enabled：是否允许用户插话打断（一般允许）

二、核心判定：每帧 user_audio 到来时做什么？
对每个 user_audio_chunk(frame) 做：

Step 1：计算简单特征
	•	rms_db（或短时能量）
	•	可选：过零率 / 频带能量（简单也行）
	•	可选：vad_hint（如果前端给了）

Step 2：判断是否处于“强回声风险窗口”
	•	如果 state == SPEAKING，默认认为回声风险高
	•	再叠加一个时间窗口：如果 now - tts_last_chunk_sent_ts < 800ms~1500ms，认为仍在播放/缓冲阶段

Step 3：做“ASR门控”决策（推荐默认策略）
A) LISTENING：正常喂给 ASR（并做 VAD/partial/final）
B) PROCESSING：通常也喂给 ASR（这样用户可以继续说、或打断），但你可以选择只做 VAD 不做 partial 输出
C) SPEAKING：进入“半开门”模式
	•	如果能量很低（静音/环境噪声）：不喂给 ASR，只更新“静音计时”
	•	如果能量明显高于噪声底（疑似用户插话）：喂给 ASR，并触发 barge-in（打断 TTS/当前回答）
	•	其余介于两者之间：可以“喂给 ASR 但不输出 partial”（仅用于判断是否插话），避免把回声文本刷到 UI

这就是后端“什么时候该 ASR 什么时候不该”的答案：主要由 state 决定，辅以能量阈值决定是否允许在 SPEAKING 时开启插话识别。

三、建议的具体阈值/规则（可调但能跑）
	1.	噪声底估计：维护一个 rolling noise floor（例如过去 2–5 秒里最低 20% 能量的均值）
	2.	speech 判定：rms_db > noise_floor_db + 10dB 认为是 speech
	3.	插话判定（SPEAKING 时）：更严格一点，比如 rms_db > noise_floor_db + 15dB 连续 200ms（10 帧*20ms）成立才算 barge-in
	4.	静音终止：in_speech 之后，连续 1.5s 没有 speech → finalize → 调 LLM

四、在代码结构上怎么实现（你现有框架怎么插）
你现在把 TTS 和 segment 的处理放在 handle_text_message（文本输入）里；语音通话需要一个 handle_audio_chunk 流程。建议这样组织：
	•	handle_audio_chunk(conv_id, stream_id, seq, pcm)：
	1.	更新 last_active
	2.	计算能量 rms_db
	3.	根据 conv_state + tts_recent_window 做 gate 决策：
	•	drop / analyze_only / feed_asr
	4.	如果 feed_asr：把帧写入 ASR streaming session
	5.	ASR 产生 partial/final 就下发 asr_partial/asr_final
	6.	VAD 判断静音累计到 1.5s 且已有 final → 触发 start_llm_reply(conv_id, final_text)
	•	start_llm_reply(...)：
	1.	state=processing
	2.	LLM 分段
	3.	state=speaking
	4.	对每 segment：并发发送 ai_text_delta + audio_chunk；audio_end 后发 board
	5.	done；state=listening

五、最关键的现实提醒
	1.	仅靠“检测回声就不回应”不够。更可靠的是“状态门控 + 插话阈值”。你不需要完美识别回声，只要在 SPEAKING 时默认不做 ASR 输出，只有检测到明显用户插话才开闸即可。
	2.	如果你的客户端能用系统 AEC（Android VOICE_COMMUNICATION + MODE_IN_COMMUNICATION），整体会稳很多；但你现在用 PCM 录制也可以先按门控策略跑通。



-----------------
# 如何让llm的语音和字幕同步出现——来自豆包语音专家的回复
你现在这版 handle_text_message 已经接近正确了，但如果你用的是豆包单向流式（v3 unidirectional stream）并且它会下行 350/351/352，那么 “跟随 speech 渲染”不应该用 transcript_pacer（打字机）去猜节奏，而应该直接用 TTSSentenceStart/End 来驱动 AI 字幕（句级同步），音频用 352 流式喂给前端。这样观感会更像豆包原生。

下面我按“需要改哪里、怎么改”给你一个整体改造方案（尽量少改你现有结构）。

⸻

你现在的代码需要改的要点（结论）
	1.	handle_text_message 里不要再用 transcript_pacer() 来模拟跟随。
改成：TTS 下行事件驱动字幕（350 开始给一句字幕，351 结束固化/下一句），音频用 352。
	2.	tts.py 的 synthesize_stream() 不能只 yield bytes 了。
改成 yield 结构化事件：("sentence_start", text) / ("audio", bytes) / ("sentence_end", None) / ("finished", meta)
（或者 yield 一个 dataclass，随你）
	3.	handle_text_message 里：对每个 segment，启动一个协程读取这些事件：
	•	sentence_start → 发送 ServerMessage.transcript_delta（或你更合适的 ai_text_sentence_start，但最小改动可以继续复用 transcript_delta：把一整句 delta 发出去即可）
	•	audio → 发送 ServerMessage.audio（带 seq）
	•	sentence_end → 可选：发 ai_sentence_end（没有也行）
	•	finished → 发送 audio_end，然后再发 board
	4.	另外一个小修：你现在 segment_start 里把 speech=segment.speech 发出去了，前端如果“收到就渲染”，会导致一开始整段字幕就出来。
如果你要“跟随”，那 segment_start 里应该 不带 speech（或带但前端不渲染），字幕全靠后续 sentence_start 推。

⸻

具体怎么改：TTSService 改造

新增一个事件流函数（保留你原来的 synthesize_stream 也行，但建议直接替换为事件流，避免双维护）：

from dataclasses import dataclass
from typing import AsyncGenerator, Optional, Union, Literal

TTSEventName = Literal["sentence_start", "sentence_end", "audio", "finished"]

@dataclass
class TTSEvent:
    name: TTSEventName
    text: Optional[str] = None
    audio: Optional[bytes] = None
    meta: Optional[dict] = None

把 synthesize_stream 改成 yield TTSEvent（核心在这段接收循环）：

async def synthesize_stream_events(
    self,
    text: str,
    speed_ratio: float = 1.0,
    volume_ratio: float = 1.0,
    interrupt_check: Optional[Callable[[], bool]] = None,
) -> AsyncGenerator[TTSEvent, None]:

    ...

    await full_client_request(websocket, json.dumps(request).encode())

    while True:
        if interrupt_check and interrupt_check():
            logger.info("TTS interrupted by user")
            break

        msg = await asyncio.wait_for(receive_message(websocket), timeout=30.0)

        # 关键：v3 单向流式的 event=350/351/352 都是“带 event number 的下行”
        if msg.type == MsgType.FullServerResponse:
            # 350/351/152 都可能走这里（看你的协议实现）
            if msg.event == EventType.TTSSentenceStart:  # 350
                # payload 是 text_binary，通常是 JSON bytes，里面有 res_params.text
                sent_text = None
                if msg.payload:
                    try:
                        obj = json.loads(msg.payload.decode("utf-8", "ignore"))
                        # 你贴的字段是 res_params.text（经文本分句后的句子）
                        sent_text = obj.get("res_params", {}).get("text") or obj.get("res_params.text")
                    except Exception:
                        # 有些实现直接是纯文本
                        sent_text = msg.payload.decode("utf-8", "ignore")
                yield TTSEvent(name="sentence_start", text=sent_text or "")

            elif msg.event == EventType.TTSSentenceEnd:  # 351
                yield TTSEvent(name="sentence_end")

            elif msg.event == EventType.SessionFinished:  # 152
                meta = None
                if msg.payload:
                    try:
                        meta = json.loads(msg.payload.decode("utf-8", "ignore"))
                    except Exception:
                        meta = {"raw": msg.payload.decode("utf-8", "ignore")}
                yield TTSEvent(name="finished", meta=meta or {})
                break

            else:
                # 其他 FullServerResponse 可忽略或 log
                _log_server_message(msg)

        elif msg.type == MsgType.AudioOnlyServer:
            # 352 通常走这里
            if msg.payload:
                yield TTSEvent(name="audio", audio=msg.payload)

        elif msg.type == MsgType.Error:
            err = ""
            if msg.payload:
                err = msg.payload.decode("utf-8", "ignore")
            raise RuntimeError(f"TTS error (code {msg.error_code}): {err}")

注意：上面的 EventType.TTSSentenceStart/End/TTSResponse 取决于你 volc_tts_protocol.py 怎么定义枚举。如果你现在只有 SessionFinished，那就把 350/351/352 加进去，并和文档一致（350/351/352/152）。

⸻

具体怎么改：handle_text_message 改造

1) segment_start 不要把整段 speech 发给前端（否则字幕会提前全部显示）

改成：

await connection_manager.send_message(
    conversation_id,
    ServerMessage.segment_start(
        segment_id=segment.segment_id,
        speech="",  # 或者不传
    ),
)

2) 删除 transcript_pacer()，改成用 TTS 事件驱动字幕 + 音频

把你现在的 if segment.speech: 里的并发逻辑替换成“单协程消费 TTS 事件流”（因为字幕和音频都来自同一条 TTS ws 流，不需要额外 pacer）：

if segment.speech:
    audio_chunk_count = 0
    seq = 0

    async for ev in ai_agent.tts.synthesize_stream_events(
        segment.speech,
        interrupt_check=lambda: interrupt_flags.get(conversation_id, False),
    ):
        if await check_interrupt(conversation_id):
            break

        if ev.name == "sentence_start":
            # 句级字幕：收到一句就显示一句（跟随 speech）
            if ev.text:
                await connection_manager.send_message(
                    conversation_id,
                    ServerMessage.transcript_delta(
                        segment_id=segment.segment_id,
                        delta=ev.text,   # 整句
                        offset=None,     # 不需要 offset 也行
                    ),
                )

        elif ev.name == "audio":
            b64 = base64.b64encode(ev.audio).decode("utf-8")
            await connection_manager.send_message(
                conversation_id,
                ServerMessage.audio(
                    audio_data=b64,
                    segment_id=segment.segment_id,
                    is_final=False,
                    seq=seq,
                    format="pcm",
                    sample_rate=ai_agent.tts.sample_rate,
                    channels=1,
                    bits_per_sample=16,
                ),
            )
            seq += 1
            audio_chunk_count += 1

        elif ev.name == "sentence_end":
            # 可选：你如果要做 UI 高亮/断行，可以发一个事件
            # await connection_manager.send_message(... ai_sentence_end ...)
            pass

        elif ev.name == "finished":
            # 结束：发 audio_end
            await connection_manager.send_message(
                conversation_id,
                ServerMessage.audio_end(
                    segment_id=segment.segment_id,
                    total_chunks=audio_chunk_count,
                ),
            )
            break

3) board 发送时机保持你现在的逻辑（audio_end 后发）

你现在已经是：先 audio_end 再 board，这个没问题。

但注意：如果 segment.speech 为空（只有 board），就直接发 board 即可。

⸻

你这版流程和豆包事件对齐后，会发生什么变化（你要的效果）
	•	AI 字幕不再是“打字机”，而是“句子级跟随”：服务端告诉你现在开始说哪句（350），你就显示那句；句子结束（351）后下一句开始再显示下一句。
	•	音频仍然是 chunk 流式（352）。
	•	board 仍然在 audio_end（152）后发（大致等说完再出现）。

这套是最贴合你贴的文档 event 语义的，也最稳，不需要猜节奏。

⸻

你必须检查的一点（否则字幕可能拿不到）

你要确认 TTSSentenceStart 的 msg.payload 里到底是什么格式：
	•	文档写的是 {...} text_binary，通常是 JSON bytes（里面有 res_params.text）。
	•	你现在 _log_server_message 用 json.loads 能打印出来的话，基本就说明是 JSON。
	•	如果不是 JSON，就直接 decode 当纯文本用。

（我在上面的实现里两种都兜底了。）

