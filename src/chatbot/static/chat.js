const els = {
  messages: document.getElementById("messages"),
  form: document.getElementById("composer"),
  input: document.getElementById("input"),
  send: document.getElementById("send"),
  customer: document.getElementById("customer"),
  reset: document.getElementById("reset"),
  status: document.getElementById("status"),
};

// Persist session_id so reload survives a chatbot restart (matters for the
// DB-backed history test). New tabs/windows start fresh because they share
// localStorage; clear via the Reset button.
function ensureSessionId() {
  let id = localStorage.getItem("chatbot_session_id");
  if (!id) {
    id = "demo-" + Math.random().toString(36).slice(2, 10);
    localStorage.setItem("chatbot_session_id", id);
  }
  return id;
}
let SESSION_ID = ensureSessionId();

function addMessage(role, text, trace, meta, opts = {}) {
  const wrapper = document.createElement("div");
  wrapper.className = `msg ${role}` + (opts.clarify ? " clarify" : "");
  const roleLabel = document.createElement("div");
  roleLabel.className = "role";
  roleLabel.textContent = role === "user" ? "You" : "TelcoBot";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrapper.appendChild(roleLabel);
  wrapper.appendChild(bubble);

  if (opts.suggestedReplies && opts.suggestedReplies.length) {
    const chips = document.createElement("div");
    chips.className = "chips";
    for (const reply of opts.suggestedReplies) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.textContent = reply;
      b.addEventListener("click", () => send(reply));
      chips.appendChild(b);
    }
    wrapper.appendChild(chips);
  }

  if (trace && trace.length) {
    const det = document.createElement("details");
    det.className = "trace";
    det.open = false;
    const sum = document.createElement("summary");
    sum.textContent = `${trace.length} tool call${trace.length === 1 ? "" : "s"}`;
    det.appendChild(sum);
    const ul = document.createElement("ul");
    for (const tc of trace) {
      const li = document.createElement("li");
      if (!tc.ok) li.classList.add("err");
      const inputStr = JSON.stringify(tc.input);
      li.textContent = `${tc.name}(${inputStr}) — ${tc.duration_ms}ms${tc.ok ? "" : " ERROR"}`;
      ul.appendChild(li);
    }
    det.appendChild(ul);
    wrapper.appendChild(det);
  }

  if (meta) {
    const m = document.createElement("div");
    m.className = "meta";
    m.textContent = meta;
    wrapper.appendChild(m);
  }

  els.messages.appendChild(wrapper);
  els.messages.scrollTop = els.messages.scrollHeight;
  return wrapper;
}

function setStatus(text, isError) {
  els.status.textContent = text;
  els.status.classList.toggle("error", !!isError);
}

async function send(message) {
  const customerId = els.customer.value;
  els.send.disabled = true;
  els.input.disabled = true;

  addMessage("user", message);
  const thinking = document.createElement("div");
  thinking.className = "msg assistant";
  thinking.innerHTML = '<div class="role">TelcoBot</div><div class="bubble"><span class="thinking">thinking</span></div>';
  els.messages.appendChild(thinking);
  els.messages.scrollTop = els.messages.scrollHeight;

  setStatus("Calling…");
  try {
    const r = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: SESSION_ID,
        customer_id: customerId,
        message,
      }),
    });
    if (!r.ok) {
      const err = await r.text();
      throw new Error(`${r.status}: ${err}`);
    }
    const data = await r.json();
    thinking.remove();
    const meta = `${data.iterations} iter · ${data.latency_ms}ms · ` +
                 `${data.tokens.prompt} in / ${data.tokens.completion} out` +
                 (data.tokens.cached ? ` · ${data.tokens.cached} cached` : "");
    const opts = {};
    if (data.awaiting_clarification) {
      opts.clarify = true;
      opts.suggestedReplies = (data.clarification && data.clarification.suggested_replies) || [];
    }
    addMessage("assistant", data.text || "(empty response)", data.tool_calls, meta, opts);
    setStatus(
      data.awaiting_clarification
        ? `Awaiting your clarification.`
        : `Ready. Last turn: ${data.latency_ms}ms.`
    );
  } catch (e) {
    thinking.remove();
    addMessage("assistant", "Error: " + e.message);
    setStatus("Error: " + e.message, true);
  } finally {
    els.send.disabled = false;
    els.input.disabled = false;
    els.input.focus();
  }
}

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  const message = els.input.value.trim();
  if (!message) return;
  els.input.value = "";
  send(message);
});

els.reset.addEventListener("click", async () => {
  els.messages.innerHTML = "";
  setStatus("Session reset. Ready.");
  try {
    await fetch("/chat/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: SESSION_ID,
        customer_id: els.customer.value,
        message: "(reset)",
      }),
    });
  } catch (_) {}
  // Rotate to a fresh session_id locally too, so the next message starts clean.
  localStorage.removeItem("chatbot_session_id");
  SESSION_ID = ensureSessionId();
});

els.customer.addEventListener("change", () => {
  els.messages.innerHTML = "";
  setStatus(`Customer switched to ${els.customer.value}. Session restarted.`);
});

els.input.focus();
