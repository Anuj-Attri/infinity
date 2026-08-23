// Catches crashes in the children subtree and shows the error inline
// instead of blanking the whole render. Errors also fire in DevTools and
// `console.error` for inspection.

import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = {
  children: ReactNode;
  label?: string;
  /** Render your own recovery UI instead of the stack trace. `reset` clears the
   *  caught error so the subtree re-renders.
   *
   *  Added for lazily-loaded tiles. The app mounts ONE boundary, around the
   *  whole TileGrid, which was right while every tile was a static import — a
   *  tile that throws on mount is a code bug and the stack is what you want.
   *  A lazy tile changes that: a dropped connection or a tab left open across a
   *  redeploy (whose content-hashed chunk now 404s) makes the import reject, and
   *  with only the outer boundary that replaces EVERY tile on the canvas with a
   *  stack trace. A local boundary keeps that failure inside the one tile. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
};
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

  reset = () => this.setState({ error: null });

  render() {
    if (this.state.error && this.props.fallback) {
      return this.props.fallback(this.state.error, this.reset);
    }
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
