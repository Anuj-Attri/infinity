// Infinity wordmark — italic serif "Infinity" with the mini orb to its left.

import Orb from "./Orb";
import { APP_NAME } from "../config/identity";

export default function Wordmark() {
  return (
    <div className="wordmark" data-tauri-drag-region>
      <span className="wordmark-mark" data-tauri-drag-region>
        <Orb size={18} breath="6s" spin="22s" trail />
      </span>
      <span className="wordmark-text" data-tauri-drag-region>{APP_NAME}</span>
    </div>
  );
}
