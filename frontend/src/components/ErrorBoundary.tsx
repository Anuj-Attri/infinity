// Catches crashes in the children subtree and shows the error inline
// instead of blanking the whole render. Errors also fire in DevTools and
// `console.error` for inspection.

import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = { children: ReactNode; label?: string };
type State = { error: Error | null };

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Surfaced in DevTools console + in the run.log if we ever pipe it.
    console.error("[ErrorBoundary]", this.props.label || "(unlabeled)", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="error-boundary">
          <div className="error-boundary-label">{this.props.label || "Render error"}</div>
          <pre className="error-boundary-msg">
            {this.state.error.name}: {this.state.error.message}
            {"\n\n"}
            {this.state.error.stack?.split("\n").slice(0, 6).join("\n")}
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}
