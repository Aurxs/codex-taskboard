// Passive decoder for the desktop IPC envelope. Codex itself acknowledges
// chunk delivery; this observer never acknowledges, consumes, or redispatches it.
(deliver) => {
  let transfer = null;
  return (event) => {
    if (event.source !== null && event.source !== window) return;
    if (event.source === window && event.origin !== window.location.origin) return;
    const message = event.data;
    if (!message || typeof message !== "object") return;
    if (message.marker !== "codex-host-chunked-message-v1") { deliver(message); return; }
    if (message.kind === "start") {
      transfer = { id: message.transferId, sequence: message.sequence + 1, stack: [], root: undefined, text: null, size: 0 };
      return;
    }
    if (!transfer || transfer.id !== message.transferId || transfer.sequence++ !== message.sequence) { transfer = null; return; }
    const state = transfer;
    const put = (value) => {
      const parent = state.stack.at(-1);
      if (!parent) state.root = value;
      else if (Array.isArray(parent.value)) parent.value.push(value);
      else {
        if (parent.key === undefined) throw new Error("Missing object key");
        Object.defineProperty(parent.value, parent.key, { value, enumerable: true, configurable: true, writable: true });
        parent.key = undefined;
      }
    };
    try {
      if (message.kind === "end") {
        transfer = null;
        if (state.stack.length === 0 && state.text === null && state.root !== undefined) deliver(state.root);
        return;
      }
      for (const token of message.tokens || []) {
        state.size += typeof token.value === "string" ? token.value.length : 1;
        if (state.size > 32 * 1024 * 1024) throw new Error("Message too large");
        switch (token.type) {
          case "object-start":
          case "array-start": {
            const value = token.type === "object-start" ? {} : [];
            put(value); state.stack.push({ value }); break;
          }
          case "container-end": if (!state.stack.pop()) throw new Error("Unmatched container"); break;
          case "key": state.stack.at(-1).key = token.value; break;
          case "value": put(token.value); break;
          case "string-start": state.text = []; state.target = token.target; break;
          case "string-chunk": state.text.push(token.value); break;
          case "string-end": {
            const text = state.text.join("");
            state.text = null;
            if (state.target === "key") state.stack.at(-1).key = text;
            else put(text);
            break;
          }
          default: throw new Error("Unknown IPC token");
        }
      }
    } catch (_) { transfer = null; }
  };
}
