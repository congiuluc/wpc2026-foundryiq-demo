import { isRecord } from "./types";

export interface StreamEvent { event: string; data: Record<string, unknown> }

export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<StreamEvent> {
  const reader = body.getReader(), decoder = new TextDecoder();
  let buffer = "";
  const parse = (frame: string): StreamEvent | null => {
    let event = "message";
    const lines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) lines.push(line.slice(5).replace(/^ /, ""));
    }
    if (!lines.length) return null;
    const data: unknown = JSON.parse(lines.join("\n"));
    if (!isRecord(data)) throw new Error("Evento streaming non valido.");
    return { event, data };
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replace(/\r\n/g, "\n");
      let index;
      while ((index = buffer.indexOf("\n\n")) >= 0) {
        const event = parse(buffer.slice(0, index));
        buffer = buffer.slice(index + 2);
        if (event) yield event;
      }
      if (done) {
        if (buffer.trim()) {
          const event = parse(buffer);
          if (event) yield event;
        }
        break;
      }
    }
  } finally {
    await reader.cancel();
    reader.releaseLock();
  }
}
