// ── Silence detection constants ───────────────────────────────────────────
const SILENCE_THRESHOLD      = 0.015;  // RMS level below which is silence
const SILENCE_DURATION_MS    = 1000;   // ms of quiet before auto-send
const MIN_SPEECH_DURATION_MS = 400;    // ignore clips shorter than this
const LEVEL_CHECK_INTERVAL_MS = 80;    // how often to poll audio level

// ── State ─────────────────────────────────────────────────────────────────
let ws = null;
let mediaRecorder = null;
let audioStream = null;
let audioContext = null;
let analyser = null;
let micSourceNode = null;
let isSpeaking = false;
let speechStartTime = null;
let silenceTimer = null;
let headerChunk = null;
let pendingChunks = [];
let processingExchange = false;
let pendingPermissionId = null;
let levelCheckInterval = null;

// Status timer state
let statusTimerInterval = null;
let statusTimerStart = null;

// Audio queue state (streaming TTS)
let audioQueue = [];
let audioIsPlaying = false;
let turnEnded = false;
let currentClaudeBubble = null;

// Playback analyser for glow animation
let playbackAnalyser = null;
let glowRafId = null;

// Track whether the user has set up the mic at least once
let micWasSetup = false;

// Track mute state
let micMuted = false;

// Track memory prompt state
let memoryEnabled = false;
let memoryPromptResolved = false;

// ── DOM ───────────────────────────────────────────────────────────────────
const statusBadge      = document.getElementById("status-badge");
const conversation     = document.getElementById("conversation");
const connectingMsg    = document.getElementById("connecting-msg");
const micBtn           = document.getElementById("mic-btn");
const permOverlay      = document.getElementById("permission-overlay");
const permDescription  = document.getElementById("permission-description");
const btnApprove       = document.getElementById("btn-approve");
const btnDeny          = document.getElementById("btn-deny");
const memoryOverlay    = document.getElementById("memory-overlay");
const btnMemoryYes     = document.getElementById("btn-memory-yes");
const btnMemoryNo      = document.getElementById("btn-memory-no");
const progressBar      = document.getElementById("progress-bar");
const speakingGlow     = document.getElementById("speaking-glow");

// ── WebSocket ─────────────────────────────────────────────────────────────
function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const token = encodeURIComponent(window.CLIO_TOKEN || "");
  ws = new WebSocket(`${proto}//${location.host}/ws?token=${token}`);

  ws.onopen = () => {
    if (connectingMsg.parentNode) connectingMsg.remove();
    micBtn.classList.remove("disabled");
    if (micWasSetup) {
      // Mic pipeline is still live — just resume listening
      startListening();
    } else {
      setStatus("connected", "Tap mic to start");
    }
  };

  ws.onclose = () => {
    setStatus("idle", "Disconnected");
    micBtn.classList.add("disabled");
    resetTurnState(); // unstick if connection drops mid-exchange
    pendingChunks = []; // discard buffered audio so we don't send stale data
    // NOTE: we intentionally do NOT teardown the audio pipeline here.
    // The mic/AudioContext/MediaRecorder stay alive through reconnects.
    setTimeout(connect, 3000);
  };

  ws.onerror = () => ws.close();

  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);
    handleServerMessage(msg);
  };
}

function handleServerMessage(msg) {
  switch (msg.type) {
    case "status":
      handleStatus(msg.state, msg.label);
      break;
    case "user_transcript":
      appendBubble("user", msg.text);
      break;
    case "audio_chunk":
      handleAudioChunk(msg);
      break;
    case "turn_end":
      handleTurnEnd();
      break;
    case "tool_result":
      appendBubble("tool", `→ ${msg.tool}: ${msg.summary}`);
      break;
    case "permission_request":
      showPermission(msg.tool_call_id, msg.tool, msg.description);
      break;
    case "memory_prompt":
      showMemoryPrompt();
      break;
    case "close_bubble":
      currentClaudeBubble = null;
      break;
    case "error":
      setStatus("error", "Error");
      resetTurnState();
      break;
  }
}

// ── Status ────────────────────────────────────────────────────────────────
function handleStatus(state, label) {
  // Never show a "speaking" status — the user can hear it
  if (state === "speaking") return;

  const defaultLabels = {
    transcribing:       "Transcribing…",
    thinking:           "Thinking…",
    planning:           "Planning…",
    working:            "Working…",
    waiting_permission: "Waiting…",
  };
  const displayLabel = label || defaultLabels[state] || state;

  const timed = state === "transcribing" || state === "thinking" || state === "planning" || state === "working";

  if (timed) {
    startStatusTimer(state, displayLabel);
    micBtn.classList.add("disabled");
    startWave();
  } else {
    stopStatusTimer();
    setStatus(state, displayLabel);
    stopWave();
  }
}

function startStatusTimer(cls, label) {
  // If we're already timing the exact same label, don't reset the clock
  if (statusTimerInterval && statusBadge._timerLabel === label) return;

  stopStatusTimer();
  statusTimerStart = Date.now();
  statusBadge._timerLabel = label;

  function tick() {
    const elapsed = Math.floor((Date.now() - statusTimerStart) / 1000);
    let timeStr = "";
    if (elapsed >= 60) {
      const m = Math.floor(elapsed / 60);
      const s = elapsed % 60;
      timeStr = ` (${m}m ${s}s)`;
    } else if (elapsed > 0) {
      timeStr = ` (${elapsed}s)`;
    }
    setStatus(cls, label + timeStr);
    statusBadge._timerCls = cls;
  }
  tick();
  statusTimerInterval = setInterval(tick, 1000);
}

function stopStatusTimer() {
  if (statusTimerInterval) {
    clearInterval(statusTimerInterval);
    statusTimerInterval = null;
  }
  statusTimerStart = null;
  statusBadge._timerLabel = null;
}

function setStatus(cls, label) {
  statusBadge.textContent = label;
  if (label) {
    statusBadge.className = cls;
  } else {
    statusBadge.className = "hidden";
  }
}

// ── Progress line ─────────────────────────────────────────────────────────
function startWave() {
  progressBar.classList.add("active");
}

function stopWave() {
  progressBar.classList.remove("active");
}

function startSpeakingGlow() {
  speakingGlow.classList.add("active");
  startGlowLoop();
}

function stopSpeakingGlow() {
  speakingGlow.classList.remove("active");
  stopGlowLoop();
  speakingGlow.style.boxShadow = "";
}

function startGlowLoop() {
  if (glowRafId) return; // already running
  const data = new Uint8Array(playbackAnalyser ? playbackAnalyser.fftSize : 0);
  function loop() {
    glowRafId = requestAnimationFrame(loop);
    if (!playbackAnalyser) return;
    playbackAnalyser.getByteTimeDomainData(data);
    // Compute RMS amplitude (0..1)
    let sum = 0;
    for (let i = 0; i < data.length; i++) {
      const v = (data[i] - 128) / 128;
      sum += v * v;
    }
    const rms = Math.sqrt(sum / data.length);
    // Map rms to glow: at silence ~18px spread, at peak ~70px spread
    const intensity = Math.min(rms * 18, 1);
    const spread  = 4  + intensity * 12;
    const blur    = 30 + intensity * 50;
    const alpha   = 0.18 + intensity * 0.32;
    speakingGlow.style.boxShadow =
      `inset 0 0 ${blur.toFixed(1)}px ${spread.toFixed(1)}px rgba(0, 229, 255, ${alpha.toFixed(3)})`;
  }
  loop();
}

function stopGlowLoop() {
  if (glowRafId) {
    cancelAnimationFrame(glowRafId);
    glowRafId = null;
  }
}

function startSession() {
  speakingGlow.classList.add("session");
}

// ── Conversation ──────────────────────────────────────────────────────────
function appendBubble(role, text) {
  const div = document.createElement("div");
  div.className = `bubble ${role}`;
  div.textContent = text;
  conversation.appendChild(div);
  conversation.scrollTop = conversation.scrollHeight;
  return div;
}

// ── Streaming audio playback ──────────────────────────────────────────────
function handleAudioChunk(msg) {
  // Clear status badge as soon as first audio chunk arrives — thinking/planning is done
  stopStatusTimer();
  setStatus("idle", "");
  stopWave();

  // Build / extend the claude bubble progressively
  if (!currentClaudeBubble) {
    currentClaudeBubble = appendBubble("claude", "");
  }
  const sep = currentClaudeBubble.textContent ? " " : "";
  currentClaudeBubble.textContent += sep + msg.text;
  conversation.scrollTop = conversation.scrollHeight;

  // Queue and start playback
  audioQueue.push(msg.audio_url);
  if (!audioIsPlaying) playNextChunk();
}

function handleTurnEnd() {
  turnEnded = true;
  if (!audioIsPlaying && audioQueue.length === 0) {
    finishTurn();
  }
}

async function playNextChunk() {
  if (audioQueue.length === 0) {
    audioIsPlaying = false;
    if (turnEnded) {
      finishTurn();
    } else {
      // turn_end hasn't arrived yet — it will call finishTurn() when it does.
      // Safety net: if it never arrives (server error, dropped message), unstick after 5s.
      setTimeout(() => {
        if (!audioIsPlaying && audioQueue.length === 0 && !turnEnded) {
          console.warn("[audio] turn_end timeout — forcing finishTurn");
          finishTurn();
        }
      }, 5000);
    }
    return;
  }

  audioIsPlaying = true;
  stopWave();
  const url = audioQueue.shift();

  try {
    // Re-resume AudioContext if iOS suspended it (e.g. after a phone call or backgrounding)
    if (audioContext.state === "suspended") await audioContext.resume();

    const response = await fetch(url);
    const arrayBuffer = await response.arrayBuffer();
    const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);

    // Create or reuse playback analyser
    if (!playbackAnalyser) {
      playbackAnalyser = audioContext.createAnalyser();
      playbackAnalyser.fftSize = 1024;
      playbackAnalyser.connect(audioContext.destination);
      startSpeakingGlow();
    }

    const source = audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(playbackAnalyser);

    // Advance to next chunk — guarded so it fires at most once.
    // The timeout covers iOS where onended can silently fail to fire.
    let advanced = false;
    const durationMs = audioBuffer.duration * 1000;
    const advance = () => {
      if (!advanced) {
        advanced = true;
        playNextChunk();
      }
    };

    source.onended = () => advance();
    // Grace period: duration + 800 ms so normal playback always wins the race
    setTimeout(() => advance(), durationMs + 800);

    source.start(0);
  } catch (err) {
    console.error("playNextChunk error:", err);
    playNextChunk(); // skip broken chunk, keep going
  }
}

function finishTurn() {
  pendingChunks = [];  // discard audio accumulated during backend processing
  stopWave();
  stopSpeakingGlow();
  stopStatusTimer();
  processingExchange = false;
  micBtn.classList.remove("disabled");
  currentClaudeBubble = null;
  turnEnded = false;
  playbackAnalyser = null;
  setStatus("idle", "");
  if (micMuted) {
    stopMicTracks();  // deferred from mute-during-speaking
  } else {
    speakingGlow.classList.add("session");
  }
}

function resetTurnState() {
  audioQueue = [];
  audioIsPlaying = false;
  turnEnded = false;
  currentClaudeBubble = null;
  processingExchange = false;
  playbackAnalyser = null;
  if (levelCheckInterval) { clearInterval(levelCheckInterval); levelCheckInterval = null; }
  stopWave();
  stopSpeakingGlow();
  stopStatusTimer();
  micBtn.classList.remove("disabled");
}

// ── Memory prompt ─────────────────────────────────────────────────────────
function showMemoryPrompt() {
  if (memoryPromptResolved) return;
  memoryOverlay.classList.add("visible");
}

function resolveMemoryPrompt(enabled) {
  memoryEnabled = enabled;
  memoryPromptResolved = true;
  memoryOverlay.classList.remove("visible");
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "memory_prompt_response", enabled }));
  }
}

btnMemoryYes.onclick = () => resolveMemoryPrompt(true);
btnMemoryNo.onclick  = () => resolveMemoryPrompt(false);

// ── Permission ────────────────────────────────────────────────────────────
function showPermission(toolCallId, tool, description) {
  pendingPermissionId = toolCallId;
  permDescription.textContent = `Clio wants to ${description}`;
  permOverlay.classList.add("visible");
  setStatus("waiting", "Waiting…");

  if ("speechSynthesis" in window) {
    const utt = new SpeechSynthesisUtterance(
      `Clio wants to ${description}. Approve or deny?`
    );
    window.speechSynthesis.speak(utt);
  }
}

function resolvePermission(approved) {
  if (!pendingPermissionId) return;
  ws.send(JSON.stringify({
    type: "permission_response",
    tool_call_id: pendingPermissionId,
    approved,
  }));
  pendingPermissionId = null;
  permOverlay.classList.remove("visible");
  setStatus("executing", "Running…");
}

btnApprove.onclick = () => resolvePermission(true);
btnDeny.onclick    = () => resolvePermission(false);

micBtn.onclick = async () => {
  if (!audioContext) {
    await setupMic();
    setStatus("idle", "");
    return;
  }
  // Immediately resume AudioContext in case iOS suspended it on tap
  if (audioContext.state === "suspended") await audioContext.resume();
  // Toggle mute
  micMuted = !micMuted;
  if (micMuted) {
    micBtn.classList.add("muted");
    speakingGlow.classList.remove("session");

    // If Clio is speaking, defer mic teardown to finishTurn() — stopping the
    // mic stream mid-playback can suspend the AudioContext on mobile.
    if (!processingExchange) {
      stopMicTracks();
    }
  } else {
    pendingChunks = [];  // discard audio recorded while muted
    micBtn.classList.remove("muted");
    speakingGlow.classList.add("session");
    // Only restart mic if it was actually stopped. If mute happened mid-speaking,
    // the mic stream is still alive — just let checkAudioLevel resume normally.
    if (!audioStream) {
      await restartMicTracks();
    }
  }
};

// ── Mic track helpers (for mute/unmute without recreating AudioContext) ───
function stopMicTracks() {
  if (levelCheckInterval) { clearInterval(levelCheckInterval); levelCheckInterval = null; }
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    try { mediaRecorder.stop(); } catch (_) {}
  }
  mediaRecorder = null;
  if (micSourceNode) {
    try { micSourceNode.disconnect(); } catch (_) {}
    micSourceNode = null;
  }
  if (audioStream) {
    audioStream.getTracks().forEach(t => t.stop());
    audioStream = null;
  }
  analyser = null;
  headerChunk = null;
  pendingChunks = [];
  isSpeaking = false;
  if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }
  micBtn.classList.remove("recording");
  speakingGlow.classList.remove("recording");
}

async function restartMicTracks() {
  try {
    audioStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      }
    });

    micSourceNode = audioContext.createMediaStreamSource(audioStream);
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 1024;
    micSourceNode.connect(analyser);

    mediaRecorder = new MediaRecorder(audioStream);
    headerChunk = null;
    pendingChunks = [];

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size === 0) return;
      if (!headerChunk) {
        headerChunk = e.data;
      } else {
        pendingChunks.push(e.data);
      }
    };

    mediaRecorder.start(100);
    startListening();
  } catch (err) {
    setStatus("error", "No mic");
    console.error("Mic restart error:", err);
  }
}

// ── Microphone setup ──────────────────────────────────────────────────────
function teardownAudio() {
  if (levelCheckInterval) { clearInterval(levelCheckInterval); levelCheckInterval = null; }
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    try { mediaRecorder.stop(); } catch (_) {}
  }
  mediaRecorder = null;
  if (audioStream) {
    audioStream.getTracks().forEach(t => t.stop());
    audioStream = null;
  }
  if (audioContext) {
    try { audioContext.close(); } catch (_) {}
    audioContext = null;
  }
  analyser = null;
  headerChunk = null;
  pendingChunks = [];
  isSpeaking = false;
  silenceTimer = null;
}

async function setupMic() {
  try {
    audioStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      }
    });
    audioContext = new AudioContext();
    await audioContext.resume();

    // Auto-resume AudioContext if iOS suspends it (e.g. on UI tap while audio is playing)
    audioContext.onstatechange = () => {
      if (audioContext && audioContext.state === "suspended") {
        audioContext.resume().catch(() => {});
      }
    };

    // Play a silent 1-frame buffer so iOS considers the AudioContext "active"
    // before any real audio arrives — prevents onended from silently failing
    // on the very first chunk of a session.
    const warmUp = audioContext.createBufferSource();
    warmUp.buffer = audioContext.createBuffer(1, 1, audioContext.sampleRate);
    warmUp.connect(audioContext.destination);
    warmUp.start(0);

    micSourceNode = audioContext.createMediaStreamSource(audioStream);
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 1024;
    micSourceNode.connect(analyser);

    mediaRecorder = new MediaRecorder(audioStream);
    headerChunk = null;
    pendingChunks = [];

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size === 0) return;
      if (!headerChunk) {
        headerChunk = e.data;
      } else {
        pendingChunks.push(e.data);
      }
    };

    mediaRecorder.start(100); // collect chunks every 100ms
    micWasSetup = true;
    startListening();
    startSession();
  } catch (err) {
    setStatus("error", "No mic");
    console.error("Mic error:", err);
  }
}

function startListening() {
  if (levelCheckInterval) clearInterval(levelCheckInterval);
  levelCheckInterval = setInterval(checkAudioLevel, LEVEL_CHECK_INTERVAL_MS);
}

function checkAudioLevel() {
  if (!analyser || processingExchange || micMuted) return;
  if (audioContext.state === "suspended") audioContext.resume();

  const data = new Uint8Array(analyser.fftSize);
  analyser.getByteTimeDomainData(data);
  const rms = getRMS(data);

  if (rms > SILENCE_THRESHOLD) {
    if (!isSpeaking) {
      isSpeaking = true;
      speechStartTime = Date.now();
      micBtn.classList.add("recording");
      speakingGlow.classList.add("recording");
    }
    if (silenceTimer) {
      clearTimeout(silenceTimer);
      silenceTimer = null;
    }
  } else {
    if (isSpeaking && !silenceTimer) {
      silenceTimer = setTimeout(() => {
        const duration = Date.now() - speechStartTime;
        isSpeaking = false;
        silenceTimer = null;
        micBtn.classList.remove("recording");
        speakingGlow.classList.remove("recording");
        if (duration >= MIN_SPEECH_DURATION_MS) {
          sendAudio();
        }
      }, SILENCE_DURATION_MS);
    }
  }
}

function getRMS(data) {
  let sum = 0;
  for (let i = 0; i < data.length; i++) {
    const val = (data[i] - 128) / 128;
    sum += val * val;
  }
  return Math.sqrt(sum / data.length);
}

function sendAudio() {
  if (processingExchange || !pendingChunks.length || !headerChunk) return;

  // Reset turn state for new exchange
  processingExchange = true;
  audioQueue = [];
  audioIsPlaying = false;
  turnEnded = false;
  currentClaudeBubble = null;

  setStatus("transcribing", "Transcribing…");
  micBtn.classList.add("disabled");

  const blob = new Blob([headerChunk, ...pendingChunks], { type: "audio/webm" });
  pendingChunks = [];

  const reader = new FileReader();
  reader.onload = () => {
    const base64 = reader.result.split(",")[1];
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "audio", data: base64 }));
    } else {
      resetTurnState();
    }
  };
  reader.readAsDataURL(blob);
}

// ── Boot ──────────────────────────────────────────────────────────────────
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/static/sw.js").catch(console.error);
}
connect();
