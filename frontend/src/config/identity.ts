// The app's name. One constant so a rename is a find-replace.
//
// A `USER_NAME` constant used to sit beside it, hardcoded to the author's first
// name. Nothing imported it — but it was a loaded gun: the obvious thing to
// reach for when a component wants to greet someone, and it would have greeted
// every account with the same name. Whose name to use is per-account server
// state now; read it from `getProfile()` in api.ts.

export const APP_NAME = "Infinity";
