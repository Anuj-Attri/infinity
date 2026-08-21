// Syntax-highlighted code block with a copy button. Light theme.

import { useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneLight } from "react-syntax-highlighter/dist/esm/styles/prism";

type Props = {
  language: string;
  value: string;
};

export default function CodeBlock({ language, value }: Props) {
  const [copied, setCopied] = useState(false);

  const onCopy = async () => {
    let ok = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(value);
        ok = true;
      }
    } catch {}
    if (!ok) {
      try {
        const ta = document.createElement("textarea");
        ta.value = value;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        ok = document.execCommand("copy");
        document.body.removeChild(ta);
      } catch {}
    }
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    }
  };

  return (
    <div className="codeblock">
      <div className="codeblock-head">
        <span>{language || "code"}</span>
        <button
          type="button"
          className={`codeblock-copy ${copied ? "copied" : ""}`}
          onClick={onCopy}
        >
          {copied ? "copied" : "copy"}
        </button>
      </div>
      <SyntaxHighlighter
        language={language || "text"}
        style={oneLight}
        PreTag="pre"
        customStyle={{ background: "transparent", margin: 0 }}
        codeTagProps={{ style: { fontFamily: "var(--font-mono)" } }}
      >
        {value.replace(/\n$/, "")}
      </SyntaxHighlighter>
    </div>
  );
}
