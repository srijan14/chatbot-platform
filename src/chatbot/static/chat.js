const els = {
  messages: document.getElementById("messages"),
  form: document.getElementById("composer"),
  input: document.getElementById("input"),
  send: document.getElementById("send"),
  customer: document.getElementById("customer"),
  reset: document.getElementById("reset"),
  status: document.getElementById("status"),
};

const SESSION_ID = "demo-" + Math.random().toString(36).slice(2, 10);

function addMessage(role, text, trace, meta) {
  const wrapper = document.createElement("div");
  wrapper.className = `msg ${role}`;
  const roleLabel = document.createElement("div");
  roleLabel.className = "role";
  roleLabel.textContent = role === "user" ? "You" : "TelcoBot";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrapper.appendChild(roleLabel);
  wrapper.appendChild(bubble);

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
    addMessage("assistant", data.text || "(empty response)", data.tool_calls, meta);
    setStatus(`Ready. Last turn: ${data.latency_ms}ms.`);
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
});

els.customer.addEventListener("change", () => {
  els.messages.innerHTML = "";
  setStatus(`Customer switched to ${els.customer.value}. Session restarted.`);
});

els.input.focus();
