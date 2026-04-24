/* Pontis Web Frontend — Client-side logic */

(function () {
  "use strict";

  // --- State ---
  let sessionId = sessionStorage.getItem("pontis_session_id");
  if (!sessionId) {
    sessionId = crypto.randomUUID();
    sessionStorage.setItem("pontis_session_id", sessionId);
  }

  const params = new URLSearchParams(window.location.search);
  const projectPath = params.get("project") || "";

  // --- DOM ---
  const messagesEl = document.getElementById("messages");
  const inputEl = document.getElementById("user-input");
  const sendBtn = document.getElementById("send-btn");
  const clearBtn = document.getElementById("clear-btn");
  const errorBanner = document.getElementById("error-banner");
  const projectPathEl = document.getElementById("project-path");
  const modelInfoEl = document.getElementById("model-info");

  let sending = false;

  // --- Init ---
  async function init() {
    if (!projectPath) {
      showError("请在 URL 中指定项目路径，例如: ?project=/path/to/project");
      return;
    }

    projectPathEl.textContent = projectPath;
    modelInfoEl.textContent = "连接中...";

    try {
      const resp = await fetch(`/api/validate?project_path=${encodeURIComponent(projectPath)}`);
      const data = await resp.json();
      if (!data.valid) {
        showError(data.error);
        return;
      }
    } catch (e) {
      showError("无法连接服务器: " + e.message);
      return;
    }

    inputEl.disabled = false;
    sendBtn.disabled = false;
    modelInfoEl.textContent = "";
    inputEl.focus();
  }

  function showError(msg) {
    errorBanner.textContent = msg;
    errorBanner.classList.remove("hidden");
  }

  // --- Send Message ---
  async function sendMessage() {
    const text = inputEl.value.trim();
    if (!text || sending) return;

    sending = true;
    inputEl.value = "";
    sendBtn.disabled = true;
    inputEl.disabled = true;
    errorBanner.classList.add("hidden");

    appendUserBubble(text);

    const typingEl = appendTyping();

    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          project_path: projectPath,
          message: text,
        }),
      });

      if (!resp.ok) {
        const err = await resp.json();
        typingEl.remove();
        appendAssistantBubble("错误: " + (err.error || resp.statusText));
        return;
      }

      // Read SSE stream
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let currentToolDetails = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // Split on double newline (SSE event boundary)
        const parts = buffer.split("\n\n");
        buffer = parts.pop();

        for (const part of parts) {
          const lines = part.split("\n").filter((l) => l.startsWith("data:"));
          for (const line of lines) {
            const raw = line.slice(5).trim();
            if (!raw) continue;
            try {
              const event = JSON.parse(raw);
              if (event.type === "tool_call") {
                if (typingEl.parentNode) typingEl.remove();
                currentToolDetails = appendToolCall(event);
              } else if (event.type === "tool_result") {
                typingEl.remove();
                updateToolResult(event);
              } else if (event.type === "done") {
                typingEl.remove();
                appendAssistantBubble(event.content);
              } else if (event.type === "error") {
                typingEl.remove();
                appendAssistantBubble("错误: " + event.content);
              }
            } catch (e) {
              // skip malformed JSON
            }
          }
        }
      }
    } catch (e) {
      typingEl.remove();
      appendAssistantBubble("网络错误: " + e.message);
    } finally {
      sending = false;
      sendBtn.disabled = false;
      inputEl.disabled = false;
      inputEl.focus();
    }
  }

  // --- Render Helpers ---

  function appendUserBubble(text) {
    const div = document.createElement("div");
    div.className = "msg msg-user";
    div.textContent = text;
    messagesEl.appendChild(div);
    scrollToBottom();
  }

  function appendAssistantBubble(content) {
    const div = document.createElement("div");
    div.className = "msg msg-assistant";
    // Simple markdown: handle code blocks and line breaks
    div.innerHTML = formatContent(content);
    messagesEl.appendChild(div);
    scrollToBottom();
  }

  function appendTyping() {
    const div = document.createElement("div");
    div.className = "typing";
    div.textContent = "思考中";
    messagesEl.appendChild(div);
    scrollToBottom();
    return div;
  }

  function appendToolCall(event) {
    const wrapper = document.createElement("div");
    wrapper.className = "tool-block";

    const details = document.createElement("details");

    const summary = document.createElement("summary");
    const nameSpan = document.createElement("span");
    nameSpan.className = "tool-name";
    nameSpan.textContent = event.name;
    const argsSpan = document.createElement("span");
    argsSpan.className = "tool-args";
    argsSpan.textContent = truncate(JSON.stringify(event.arguments), 120);

    summary.appendChild(nameSpan);
    summary.appendChild(argsSpan);
    details.appendChild(summary);

    const resultPre = document.createElement("pre");
    resultPre.className = "tool-result tool-loading";
    resultPre.id = "result-" + event.id;
    resultPre.textContent = "执行中...";
    details.appendChild(resultPre);

    wrapper.appendChild(details);
    messagesEl.appendChild(wrapper);
    scrollToBottom();
    return details;
  }

  function updateToolResult(event) {
    const el = document.getElementById("result-" + event.id);
    if (!el) return;
    el.classList.remove("tool-loading");
    el.textContent = truncate(event.result, 3000);
  }

  function formatContent(text) {
    // Escape HTML
    let html = text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    // Code blocks
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, "<pre><code>$2</code></pre>");
    // Inline code
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    // Line breaks
    html = html.replace(/\n/g, "<br>");
    return html;
  }

  function truncate(str, maxLen) {
    if (!str || str.length <= maxLen) return str;
    return str.slice(0, maxLen) + "...";
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    });
  }

  // --- Clear Session ---
  clearBtn.addEventListener("click", async () => {
    if (!confirm("清除当前会话的对话历史？")) return;
    try {
      await fetch(
        `/api/sessions/${sessionId}?project_path=${encodeURIComponent(projectPath)}`,
        { method: "DELETE" }
      );
    } catch (_) {}
    // New session
    sessionId = crypto.randomUUID();
    sessionStorage.setItem("pontis_session_id", sessionId);
    messagesEl.innerHTML = "";
  });

  // --- Event Listeners ---
  sendBtn.addEventListener("click", sendMessage);
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // --- Start ---
  init();
})();
