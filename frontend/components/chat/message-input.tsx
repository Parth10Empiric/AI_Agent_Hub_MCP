"use client";

import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export function MessageInput({
  onSend,
  onCancel,
  busy,
  disabled,
  placeholder = "Ask your agent...",
}: {
  onSend: (text: string) => void;
  onCancel?: () => void;
  busy: boolean;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  function submit() {
    const text = value.trim();
    if (!text || busy || disabled) return;

    onSend(text);
    setValue("");

    // Keep focus in the box so the next question can be typed straight
    // away. Losing focus after every send makes a chat feel broken.
    ref.current?.focus();
  }

  return (
    <form
      className="flex items-end gap-2 border-t bg-background p-3"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <Textarea
        ref={ref}
        rows={1}
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          // Enter sends, Shift+Enter makes a new line - the convention
          // every chat app uses. Checking shiftKey is what separates
          // "send" from "write a second paragraph".
          //
          // isComposing guards IME input: while typing Hindi, Japanese
          // or Chinese through a candidate window, Enter CONFIRMS the
          // character. Sending on that keystroke would fire the message
          // mid-word, every time.
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
        className="max-h-40 min-h-10 flex-1 resize-none"
      />

      {busy && onCancel ? (
        <Button type="button" variant="outline" onClick={onCancel}>
          Stop
        </Button>
      ) : (
        <Button type="submit" disabled={!value.trim() || busy || disabled}>
          Send
        </Button>
      )}
    </form>
  );
}
